import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.models.contracts import ModelStreamChunk
from story_engine.submission.service import (
    SubmissionDraft,
    fog_harbor_submission,
)

AUTH = {"Authorization": "Bearer test-token"}


def _formal_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*.md"))
        if ".story-engine" not in path.parts
    }


class StoryTurnTransport:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []
        self._lock = Lock()

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        prompt = payload["messages"][0]["content"]
        assert isinstance(prompt, str)
        model = payload["model"]
        if model == "qwen3-8b" and '"id": "chen-mo"' in prompt:
            content = {
                "character_id": "chen-mo",
                "action": "检查灯塔机械装置",
                "target": "灯塔",
                "goal": "查明灯塔熄灭原因",
                "knowledge_basis": ["secret:chen-father-disappearance"],
                "recognized_risk": "可能暴露自己的调查",
            }
        elif model == "qwen3-8b" and '"id": "lin-lan"' in prompt:
            content = {
                "character_id": "lin-lan",
                "action": "呼叫客船降低航速",
                "target": "近港客船",
                "goal": "让客船安全进入雾港",
                "knowledge_basis": ["secret:lin-unfiled-duty-roster"],
                "recognized_risk": "备用航标可能不足",
            }
        elif model == "gpt-5-mini":
            content = {
                "summary": "陈默检查装置。林岚要求客船降低航速。",
                "public_results": ["客船开始减速"],
                "world_changes": [
                    {
                        "target_type": "world",
                        "target_id": "world",
                        "field": "world_variables.round",
                        "old_value": 0,
                        "new_value": 1,
                        "reason": "统一结算两个角色的行动",
                    }
                ],
                "unresolved_consequences": ["灯塔仍未恢复"],
            }
        else:
            raise AssertionError("unexpected Story ModelGateway request")
        with self._lock:
            self.calls.append(payload)
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
        raise AssertionError("turn generation does not use streaming yet")


class SubmissionTransport:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(payload)
        content = {
            "reply": "初始世界已经具备运行条件, 你可以继续调整或创建项目。",
            "draft": fog_harbor_submission().model_dump(mode="json"),
            "review": {
                "mode": "submission_review",
                "passed": True,
                "summary": "创作方向、世界压力和两个角色的知识边界明确。",
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
        raise AssertionError("submission discussion does not stream yet")


def test_submission_message_uses_editor_profile_without_creating_project(
    tmp_path: Path,
) -> None:
    transport = SubmissionTransport()
    client = TestClient(
        create_app(
            EngineSettings(session_token="test-token", projects_root=tmp_path),
            model_transport=transport,
        )
    )

    response = client.post(
        "/projects/fog-harbor/submission/messages",
        headers=AUTH,
        json={
            "draft": SubmissionDraft(id="fog-harbor").model_dump(mode="json"),
            "messages": [
                {
                    "role": "user",
                    "content": "我想写一个暴风雨中的港口悬疑故事。",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["runnable"] is True
    assert response.json()["missing_requirements"] == []
    assert response.json()["draft"]["characters"][0]["known_fact_ids"] == [
        "secret:chen-father-disappearance"
    ]
    assert not (tmp_path / "fog-harbor").exists()
    assert len(transport.calls) == 1
    assert transport.calls[0]["model"] == "gpt-5-mini"
    assert transport.calls[0]["messages"][0]["role"] == "system"
    assert transport.calls[0]["response_format"]["type"] == "json_schema"


def test_submission_message_rejects_path_and_draft_id_mismatch(
    tmp_path: Path,
) -> None:
    transport = SubmissionTransport()
    client = TestClient(
        create_app(
            EngineSettings(session_token="test-token", projects_root=tmp_path),
            model_transport=transport,
        )
    )

    response = client.post(
        "/projects/north-star/submission/messages",
        headers=AUTH,
        json={
            "draft": SubmissionDraft(id="fog-harbor").model_dump(mode="json"),
            "messages": [{"role": "user", "content": "继续"}],
        },
    )

    assert response.status_code == 409
    assert transport.calls == []
    assert not any(tmp_path.iterdir())


def test_submission_message_without_jan_bridge_fails_without_project(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            EngineSettings(session_token="test-token", projects_root=tmp_path)
        )
    )

    response = client.post(
        "/projects/north-star/submission/messages",
        headers=AUTH,
        json={
            "draft": SubmissionDraft(id="north-star").model_dump(mode="json"),
            "messages": [{"role": "user", "content": "我想写科幻故事。"}],
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "model_configuration_error"
    assert "Jan model runtime bridge" in response.json()["detail"]["message"]
    assert not any(tmp_path.iterdir())


def test_project_and_turn_approval_flow(tmp_path: Path) -> None:
    transport = StoryTurnTransport()
    client = TestClient(
        create_app(
            EngineSettings(
                session_token="test-token",
                projects_root=tmp_path,
            ),
            model_transport=transport,
        )
    )

    created = client.post(
        "/submissions/finalize",
        headers=AUTH,
        json=fog_harbor_submission().model_dump(mode="json"),
    )
    assert created.status_code == 201
    assert created.json()["project"]["id"] == "fog-harbor"

    turn = client.post(
        "/projects/fog-harbor/turns/generate",
        headers=AUTH,
        json={},
    )
    assert turn.status_code == 201
    assert turn.json()["status"] == "reviewed"
    assert len(turn.json()["intents"]) == 2
    assert sorted(call["model"] for call in transport.calls) == [
        "gpt-5-mini",
        "qwen3-8b",
        "qwen3-8b",
    ]

    approved = client.post(
        "/projects/fog-harbor/turns/turn-000001/confirm",
        headers=AUTH,
    )
    assert approved.status_code == 200
    assert approved.json()["candidate"]["status"] == "committed"
    assert approved.json()["event"]["id"] == "event-000001"

    project = client.get("/projects/fog-harbor", headers=AUTH)
    assert project.status_code == 200
    assert project.json()["world"]["version"] == 1
    assert all(character["version"] == 1 for character in project.json()["characters"])


def test_turn_api_exposes_only_revision_confirmation_and_discard_actions(
    tmp_path: Path,
) -> None:
    app = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path)
    )
    paths = app.openapi()["paths"]

    assert "/projects/{project_id}/turns/{turn_id}/request-revision" in paths
    assert "/projects/{project_id}/turns/{turn_id}/confirm" in paths
    assert "/projects/{project_id}/turns/{turn_id}/discard" in paths
    assert "/projects/{project_id}/turns/{turn_id}/review" not in paths
    assert "/projects/{project_id}/turns/{turn_id}/approve" not in paths
    assert "/projects/{project_id}/turns" not in paths


def test_unrunnable_submission_returns_conflict_without_project(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            EngineSettings(session_token="test-token", projects_root=tmp_path)
        )
    )
    payload = fog_harbor_submission().model_dump(mode="json")
    payload["pressures"] = []
    for character in payload["characters"]:
        character["current_goal"] = "等待天亮"

    response = client.post("/submissions/finalize", headers=AUTH, json=payload)

    assert response.status_code == 409
    assert not (tmp_path / "fog-harbor").exists()


def test_project_routes_require_session_token(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            EngineSettings(
                session_token="test-token",
                projects_root=tmp_path,
            )
        )
    )

    response = client.post(
        "/submissions/finalize",
        json=fog_harbor_submission().model_dump(mode="json"),
    )

    assert response.status_code == 401
    assert not (tmp_path / "fog-harbor").exists()


def test_turn_generation_without_the_tauri_model_bridge_fails_explicitly(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(
            EngineSettings(
                session_token="test-token",
                projects_root=tmp_path,
            )
        )
    )
    created = client.post(
        "/submissions/finalize",
        headers=AUTH,
        json=fog_harbor_submission().model_dump(mode="json"),
    )
    assert created.status_code == 201
    root = tmp_path / "fog-harbor"
    before = _formal_bytes(root)

    response = client.post(
        "/projects/fog-harbor/turns/generate",
        headers=AUTH,
        json={},
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "model_configuration_error"
    assert "Jan model runtime bridge" in response.json()["detail"]["message"]
    assert _formal_bytes(root) == before
    assert not (root / ".story-engine/turns/turn-000001.json").exists()
