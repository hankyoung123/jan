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
    def __init__(self, *, review_passed: bool = True) -> None:
        self.calls: list[Mapping[str, Any]] = []
        self.review_passed = review_passed

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
    example = SubmissionDraft.model_validate(json.loads(example_json)["draft"])
    current = SubmissionDraft.model_validate(json.loads(current_json))
    assert example.missing_requirements() == ()
    assert current == SubmissionDraft(id="fog-harbor")
    assert task_context.count(
        json.dumps(current.model_dump(mode="json"), ensure_ascii=False)
    ) == 1
    restored = _client(tmp_path).get(
        "/projects/fog-harbor/submission",
        headers=AUTH,
    )
    assert restored.status_code == 200
    assert len(restored.json()["messages"]) == 2
    assert restored.json()["messages"][-1]["parts"] == response.json()["message"][
        "parts"
    ]


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
            "messages": [_user_message("整理完整投稿设定。")],
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
