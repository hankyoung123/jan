from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.registry import ProfileRegistry

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class ApiTransport:
    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
        first_content_timeout_seconds: float | None = None,
    ) -> Mapping[str, Any]:
        del payload, timeout_seconds, first_content_timeout_seconds
        return {
            "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[ModelStreamChunk]:
        del payload, timeout_seconds
        yield ModelStreamChunk(delta="ready")
        yield ModelStreamChunk(done=True)


def _settings(tmp_path: Path) -> EngineSettings:
    return EngineSettings(
        session_token=TOKEN,
        projects_root=tmp_path / "projects",
        model_registry_path=tmp_path / "config" / "agents.json",
        model_base_url=None,
        model_api_key=None,
    )


def _registry(tmp_path: Path) -> ProfileRegistry:
    registry = ProfileRegistry(tmp_path / "config" / "agents.json")
    writer = registry.get_profile("writer").model_copy(
        update={"model": "test-provider/test-writer"}
    )
    registry.upsert_profile(writer)
    return registry


def _client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(
            _settings(tmp_path),
            model_registry=_registry(tmp_path),
            model_transport=ApiTransport(),
        )
    )


def test_agent_catalog_is_the_only_configuration_surface(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.get("/agent-profiles").status_code == 401
    response = client.get("/agent-profiles/catalog", headers=HEADERS)

    assert response.status_code == 200
    assert len(response.json()["profiles"]) == 6
    assert len(client.get("/agent-profiles", headers=HEADERS).json()) == 6
    assert client.get("/models/profiles", headers=HEADERS).status_code == 404
    assert client.get(
        "/projects/fog-harbor/model-policy",
        headers=HEADERS,
    ).status_code == 404


def test_agent_profile_patch_updates_prompt_and_parameters(tmp_path: Path) -> None:
    client = _client(tmp_path)

    patched = client.patch(
        "/agent-profiles/writer",
        headers=HEADERS,
        json={
            "default_system_prompt": "Use spare, exact prose.",
            "max_output_tokens": None,
            "reasoning_effort": "high",
            "timeout_seconds": 300,
        },
    )

    assert patched.status_code == 200
    body = patched.json()
    assert body["agent_type"] == "writer"
    assert body["default_system_prompt"] == "Use spare, exact prose."
    assert body["max_output_tokens"] is None
    assert body["reasoning_effort"] == "high"
    assert body["model"] == "test-provider/test-writer"
    assert client.patch(
        "/agent-profiles/writer", headers=HEADERS, json={}
    ).status_code == 422


def test_unknown_agent_type_cannot_be_created(tmp_path: Path) -> None:
    client = _client(tmp_path)

    assert client.patch(
        "/agent-profiles/custom", headers=HEADERS, json={"name": "Custom"}
    ).status_code == 422
    assert client.put(
        "/agent-profiles/custom", headers=HEADERS, json={}
    ).status_code == 405


def test_complete_uses_configured_agent_through_bridge(tmp_path: Path) -> None:
    completed = _client(tmp_path).post(
        "/models/complete",
        headers=HEADERS,
        json={
            "profile_id": "writer",
            "task_type": "writer",
            "messages": [{"role": "user", "content": "test"}],
            "max_output_tokens": 64,
            "timeout_seconds": 5,
        },
    )

    assert completed.status_code == 200
    assert completed.json()["content"] == "ready"
    assert completed.json()["model_ref"] == "test-provider/test-writer"


def test_missing_desktop_bridge_uses_stable_configuration_error(
    tmp_path: Path,
) -> None:
    client = TestClient(
        create_app(_settings(tmp_path), model_registry=_registry(tmp_path))
    )

    response = client.post(
        "/models/complete",
        headers=HEADERS,
        json={
            "profile_id": "writer",
            "task_type": "writer",
            "messages": [{"role": "user", "content": "test"}],
            "max_output_tokens": 64,
            "timeout_seconds": 5,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "model_configuration_error"
