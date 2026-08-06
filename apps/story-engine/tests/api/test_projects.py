import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from profile_factory import agent_profile as _profile

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.gateway import ModelPartSink
from story_engine.models.registry import ProfileRegistry
from story_engine.submission.service import SubmissionDraft, fog_harbor_submission

AUTH = {"Authorization": "Bearer test-token"}


def _user_message(text: str) -> dict[str, Any]:
    return {
        "id": "message:user-1",
        "role": "user",
        "parts": [{"type": "text", "text": text}],
        "metadata": {
            "callId": "submission:user-1",
            "agentType": "user",
            "agentName": "User",
            "taskLabel": "投稿讨论",
            "createdAt": "2026-08-06T00:00:00Z",
        },
    }


class SubmissionTransport:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
        first_content_timeout_seconds: float | None = None,
        part_sink: ModelPartSink | None = None,
    ) -> Mapping[str, Any]:
        del timeout_seconds, first_content_timeout_seconds
        self.calls.append(payload)
        package = fog_harbor_submission()
        character_refs = {
            character.id: index for index, character in enumerate(package.characters)
        }
        fact_refs = {fact.id: index for index, fact in enumerate(package.facts)}
        content = {
            "reply": "初始世界已经具备运行条件。",
            "delta": {
                "title": package.title,
                "genre": package.genre,
                "theme": package.theme,
                "tone": package.tone,
                "world_rules": list(package.world_rules),
                "facts": [
                    {
                        "statement": fact.statement,
                        "visibility": fact.visibility,
                        "known_by": [character_refs[item] for item in fact.known_by],
                        "supersedes_ref": (
                            fact_refs[fact.supersedes_fact_id]
                            if fact.supersedes_fact_id is not None
                            else None
                        ),
                    }
                    for fact in package.facts
                ],
                "characters": [
                    {
                        "display_name": character.display_name,
                        "identity": character.identity,
                        "core_desire": character.core_desire,
                        "current_goal": character.current_goal,
                        "location": character.location,
                        "emotional_state": character.emotional_state,
                        "resources": list(character.resources),
                    }
                    for character in package.characters
                ],
                "initial_time": package.initial_time,
                "initial_location": package.initial_location,
                "initial_incident": package.initial_incident,
                "pressures": list(package.pressures),
            },
        }
        if part_sink is not None:
            part_sink("reasoning", "核对世界规则与角色知识边界。")
            part_sink("text", json.dumps(content, ensure_ascii=False))
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(content, ensure_ascii=False),
                        "reasoning_content": "核对世界规则与角色知识边界。",
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
            "messages": [_user_message("写一个港口悬疑故事。")],
        },
    )

    assert response.status_code == 200
    assert response.json()["runnable"] is True
    assert response.json()["message"]["parts"] == [
        {"type": "reasoning", "text": "核对世界规则与角色知识边界。"},
        {"type": "text", "text": "初始世界已经具备运行条件。"},
    ]
    root = tmp_path / "fog-harbor"
    assert not (root / "project.md").exists()
    assert (root / "submission/conversation.jsonl").is_file()
    assert (root / "submission/draft.md").is_file()
    assert (root / "submission/status.json").is_file()
    assert transport.calls[0]["model"] == "test-provider/test-submission-editor"
    task_context = next(
        message["content"]
        for message in transport.calls[0]["messages"]
        if isinstance(message["content"], str)
        and "EXAMPLE JSON OUTPUT:" in message["content"]
    )
    example_json, current_json = task_context.split(
        "EXAMPLE JSON OUTPUT: ",
        maxsplit=1,
    )[1].split(" Current draft: ", maxsplit=1)
    example = json.loads(example_json)["delta"]
    current = SubmissionDraft.model_validate(json.loads(current_json))
    assert set(example) == {
        "title",
        "genre",
        "theme",
        "tone",
        "world_rules",
        "facts",
        "characters",
        "initial_time",
        "initial_location",
        "initial_incident",
        "pressures",
    }
    assert "id" not in example
    assert all("id" not in item for item in example["characters"])
    assert all("known_fact_ids" not in item for item in example["characters"])
    assert all("id" not in item for item in example["facts"])
    assert current == SubmissionDraft(id="fog-harbor")
    assert (
        task_context.count(
            json.dumps(current.model_dump(mode="json"), ensure_ascii=False)
        )
        == 1
    )
    restored = _client(tmp_path).get(
        "/projects/fog-harbor/submission",
        headers=AUTH,
    )
    assert restored.status_code == 200
    assert len(restored.json()["messages"]) == 2
    assert (
        restored.json()["messages"][-1]["parts"] == response.json()["message"]["parts"]
    )


def test_submission_image_part_reaches_provider_payload(tmp_path: Path) -> None:
    transport = SubmissionTransport()
    message = _user_message("")
    message["parts"] = [
        {
            "type": "file",
            "mediaType": "image/png",
            "url": "data:image/png;base64,aGVsbG8=",
            "filename": "harbor.png",
        }
    ]

    response = _client(tmp_path, transport=transport).post(
        "/projects/fog-harbor/submission/messages",
        headers=AUTH,
        json={
            "draft": SubmissionDraft(id="fog-harbor").model_dump(mode="json"),
            "messages": [message],
        },
    )

    assert response.status_code == 200
    provider_message = transport.calls[0]["messages"][-1]
    assert provider_message == {
        "role": "user",
        "content": [
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,aGVsbG8="},
            }
        ],
    }


def test_finalize_uses_existing_submission_workspace_and_marks_it_finalized(
    tmp_path: Path,
) -> None:
    transport = SubmissionTransport()
    client = _client(tmp_path, transport=transport)
    discussed = client.post(
        "/projects/fog-harbor/submission/messages",
        headers=AUTH,
        json={
            "draft": SubmissionDraft(id="fog-harbor").model_dump(mode="json"),
            "messages": [_user_message("写一个港口悬疑故事。")],
        },
    )
    assert discussed.status_code == 200

    finalized = client.post(
        "/submissions/finalize",
        headers=AUTH,
        json=fog_harbor_submission().model_dump(mode="json"),
    )

    assert finalized.status_code == 201
    assert (tmp_path / "fog-harbor/project.md").is_file()
    restored = client.get("/projects/fog-harbor/submission", headers=AUTH)
    assert restored.status_code == 200
    assert restored.json()["status"]["finalized"] is True


def test_complete_submission_review_and_runnable_are_computed_locally(
    tmp_path: Path,
) -> None:
    response = _client(tmp_path, transport=SubmissionTransport()).post(
        "/projects/fog-harbor/submission/messages",
        headers=AUTH,
        json={
            "draft": SubmissionDraft(id="fog-harbor").model_dump(mode="json"),
            "messages": [_user_message("整理完整投稿设定。")],
        },
    )

    assert response.status_code == 200
    assert response.json()["review"]["passed"] is True
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
