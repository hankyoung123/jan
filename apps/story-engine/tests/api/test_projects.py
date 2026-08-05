import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from profile_factory import agent_profile as _profile

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.registry import ProfileRegistry
from story_engine.submission.service import SubmissionDraft, fog_harbor_submission

AUTH = {"Authorization": "Bearer test-token"}


class SubmissionTransport:
    def __init__(self, *, review_passed: bool = True) -> None:
        self.calls: list[Mapping[str, Any]] = []
        self.review_passed = review_passed

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(payload)
        content = {
            "reply": "初始世界已经具备运行条件。",
            "draft": fog_harbor_submission().model_dump(mode="json"),
            "review": {
                "mode": "submission_review",
                "passed": self.review_passed,
                "summary": "创作方向、压力和知识边界明确。",
                "issues": [],
            },
        }
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(content, ensure_ascii=False),
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
        raise AssertionError("submission discussion does not stream")


def _client(
    tmp_path: Path,
    *,
    transport: SubmissionTransport | None = None,
) -> TestClient:
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="submission_editor",
            task_type="submission_editor",
            model_ref="test-provider/test-submission-editor",
        )
    )
    return TestClient(
        create_app(
            EngineSettings(
                session_token="test-token",
                projects_root=tmp_path,
                model_registry_path=registry.path,
            ),
            model_registry=registry,
            model_transport=transport,
        )
    )


def test_submission_message_uses_submission_editor_without_creating_project(
    tmp_path: Path,
) -> None:
    transport = SubmissionTransport()
    response = _client(tmp_path, transport=transport).post(
        "/projects/fog-harbor/submission/messages",
        headers=AUTH,
        json={
            "draft": SubmissionDraft(id="fog-harbor").model_dump(mode="json"),
            "messages": [{"role": "user", "content": "写一个港口悬疑故事。"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["runnable"] is True
    assert not (tmp_path / "fog-harbor").exists()
    assert transport.calls[0]["model"] == "test-provider/test-submission-editor"


def test_complete_submission_is_runnable_when_model_review_flag_is_false(
    tmp_path: Path,
) -> None:
    response = _client(
        tmp_path,
        transport=SubmissionTransport(review_passed=False),
    ).post(
        "/projects/fog-harbor/submission/messages",
        headers=AUTH,
        json={
            "draft": SubmissionDraft(id="fog-harbor").model_dump(mode="json"),
            "messages": [{"role": "user", "content": "整理完整投稿设定。"}],
        },
    )

    assert response.status_code == 200
    assert response.json()["review"]["passed"] is False
    assert response.json()["missing_requirements"] == []
    assert response.json()["runnable"] is True


def test_project_create_open_get_and_close(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = client.post(
        "/submissions/finalize",
        headers=AUTH,
        json=fog_harbor_submission().model_dump(mode="json"),
    )
    opened = client.post("/projects/fog-harbor/open", headers=AUTH)
    fetched = client.get("/projects/fog-harbor", headers=AUTH)
    closed = client.post("/projects/fog-harbor/close", headers=AUTH)

    assert created.status_code == 201
    assert opened.status_code == 200
    assert fetched.json()["project"]["id"] == "fog-harbor"
    assert closed.json()["status"] == "closed"


def test_unrunnable_submission_returns_conflict_without_project(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = fog_harbor_submission().model_dump(mode="json")
    payload["pressures"] = []
    for character in payload["characters"]:
        character["current_goal"] = "等待天亮"

    response = client.post("/submissions/finalize", headers=AUTH, json=payload)

    assert response.status_code == 409
    assert not (tmp_path / "fog-harbor").exists()


def test_old_batch_turn_api_is_absent(tmp_path: Path) -> None:
    paths = _client(tmp_path).app.openapi()["paths"]

    assert not any("/turns" in path for path in paths)
    assert "/projects/{project_id}/simulations" in paths


def test_project_routes_require_session_token(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/submissions/finalize",
        json=fog_harbor_submission().model_dump(mode="json"),
    )

    assert response.status_code == 401
    assert not (tmp_path / "fog-harbor").exists()
