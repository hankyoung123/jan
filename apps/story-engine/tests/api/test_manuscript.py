import json
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.domain.models import StoryEvent
from story_engine.models.contracts import ModelStreamChunk
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.event_store import EventStore

AUTH = {"Authorization": "Bearer test-token"}


class ManuscriptTransport:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        prompt = str(payload["messages"][0]["content"])
        self.prompts.append(prompt)
        if "Story Engine Writer" in prompt:
            content: dict[str, Any] = {
                "title": "灯芯槽的刮痕",
                "body": "陈默抵达灯塔, 并在灯芯槽上发现新鲜刮痕。",
            }
        elif "黑色纤维" in prompt:
            content = {
                "review": {
                    "mode": "manuscript_review",
                    "passed": False,
                    "summary": "正文包含尚未进入 Canon 的新事实。",
                    "issues": [
                        {
                            "code": "new_fact_requires_amendment",
                            "message": "新增事实必须先确认。",
                            "severity": "blocking",
                            "evidence_ids": [],
                        }
                    ],
                },
                "new_facts": ["刮痕末端沾着黑色纤维"],
            }
        else:
            content = {
                "review": {
                    "mode": "manuscript_review",
                    "passed": True,
                    "summary": "正文仅使用了已确认事实。",
                    "issues": [],
                },
                "new_facts": [],
            }
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(content, ensure_ascii=False)
                    },
                    "finish_reason": "stop",
                }
            ]
        }

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[ModelStreamChunk]:
        del payload, timeout_seconds
        if False:
            yield ModelStreamChunk()
        raise AssertionError("manuscript workflow does not stream")


def _client(tmp_path: Path) -> tuple[TestClient, ManuscriptTransport]:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    root = tmp_path / "fog-harbor"
    EventStore(root).append(
        StoryEvent(
            id="event-000001",
            sequence=1,
            occurred_at=datetime(2026, 7, 31, 4, 0, tzinfo=UTC),
            summary="陈默抵达灯塔并发现灯芯槽上的新鲜刮痕。",
            participants=("chen-mo",),
            public_results=("灯芯槽上有新鲜刮痕",),
            source_turn_id="turn-000001",
            approved_by_user=True,
        )
    )
    transport = ManuscriptTransport()
    return (
        TestClient(
            create_app(
                EngineSettings(session_token="test-token", projects_root=tmp_path),
                model_transport=transport,
            )
        ),
        transport,
    )


def test_scene_api_generates_lists_and_saves_reviewed_markdown(tmp_path: Path) -> None:
    client, transport = _client(tmp_path)

    generated = client.post(
        "/projects/fog-harbor/scenes/generate",
        headers=AUTH,
        json={"event_ids": ["event-000001"], "chapter_id": "chapter-03"},
    )
    assert generated.status_code == 201
    draft = generated.json()
    assert draft["status"] == "reviewed"
    assert draft["source_event_ids"] == ["event-000001"]
    assert {item["task"] for item in draft["retrieval_evidence"]} == {
        "writer",
        "editor",
    }
    assert all(
        item["chunk_id"] and item["source_path"]
        for item in draft["retrieval_evidence"]
    )
    assert "event-000001" in transport.prompts[0]
    assert "hidden_results" not in transport.prompts[0]
    assert all("Retrieval evidence" in prompt for prompt in transport.prompts[:2])
    assert all('"chunk_id"' in prompt for prompt in transport.prompts[:2])

    listed = client.get("/projects/fog-harbor/scenes", headers=AUTH)
    loaded = client.get(
        "/projects/fog-harbor/scenes/scene-000001", headers=AUTH
    )
    assert listed.status_code == loaded.status_code == 200
    assert [item["id"] for item in listed.json()] == ["scene-000001"]
    assert loaded.json()["body"] == draft["body"]

    saved = client.put(
        "/projects/fog-harbor/scenes/scene-000001",
        headers=AUTH,
        json={
            "title": draft["title"],
            "body": draft["body"],
            "expected_revision": draft["revision"],
            "expected_scene_version": draft["base_scene_version"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["status"] == "saved"
    assert saved.json()["scene"]["version"] == 1
    assert saved.json()["draft"]["base_scene_version"] == 1
    assert saved.json()["draft"]["revision"] == 0

    exported = client.get(
        "/projects/fog-harbor/manuscript/export", headers=AUTH
    )
    assert exported.status_code == 200
    assert exported.json()["filename"] == "fog-harbor-manuscript.md"
    assert "## 灯芯槽的刮痕" in exported.json()["markdown"]


def test_scene_api_requires_explicit_amendment_confirmation(tmp_path: Path) -> None:
    client, _transport = _client(tmp_path)
    draft = client.post(
        "/projects/fog-harbor/scenes/generate",
        headers=AUTH,
        json={"event_ids": ["event-000001"], "chapter_id": "chapter-03"},
    ).json()

    reviewed = client.put(
        "/projects/fog-harbor/scenes/scene-000001",
        headers=AUTH,
        json={
            "title": draft["title"],
            "body": f'{draft["body"]} 刮痕末端沾着黑色纤维。',
            "expected_revision": draft["revision"],
            "expected_scene_version": draft["base_scene_version"],
        },
    )

    assert reviewed.status_code == 200
    result = reviewed.json()
    assert result["status"] == "amendment_required"
    assert result["scene"] is None
    assert result["amendment"]["status"] == "pending"
    assert not any((tmp_path / "fog-harbor/scenes").glob("*.md"))

    mismatched = client.post(
        "/projects/fog-harbor/scenes/scene-999999/amendments/"
        f'{result["amendment"]["id"]}/confirm',
        headers=AUTH,
    )
    assert mismatched.status_code == 409
    assert not any((tmp_path / "fog-harbor/scenes").glob("*.md"))
    assert len(EventStore(tmp_path / "fog-harbor").list_events()) == 1

    confirmed = client.post(
        "/projects/fog-harbor/scenes/scene-000001/amendments/"
        f'{result["amendment"]["id"]}/confirm',
        headers=AUTH,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["amendment"]["status"] == "committed"
    assert confirmed.json()["event"]["approved_by_user"] is True
    assert any((tmp_path / "fog-harbor/scenes").glob("*.md"))


def test_scene_api_rejects_stale_revision_without_formal_write(tmp_path: Path) -> None:
    client, _transport = _client(tmp_path)
    draft = client.post(
        "/projects/fog-harbor/scenes/generate",
        headers=AUTH,
        json={"event_ids": ["event-000001"], "chapter_id": "chapter-03"},
    ).json()

    response = client.put(
        "/projects/fog-harbor/scenes/scene-000001",
        headers=AUTH,
        json={
            "title": draft["title"],
            "body": draft["body"],
            "expected_revision": 99,
            "expected_scene_version": draft["base_scene_version"],
        },
    )

    assert response.status_code == 409
    assert not any((tmp_path / "fog-harbor/scenes").glob("*.md"))
