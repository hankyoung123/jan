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
    ) -> Mapping[str, Any]:
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
        yield ModelStreamChunk(delta="ready")
        yield ModelStreamChunk(done=True)


def _settings(tmp_path: Path) -> EngineSettings:
    return EngineSettings(
        session_token=TOKEN,
        projects_root=tmp_path / "projects",
        model_registry_path=tmp_path / "config" / "models.json",
        model_base_url=None,
        model_api_key=None,
    )


def _client(tmp_path: Path) -> TestClient:
    path = tmp_path / "config" / "models.json"
    return TestClient(
        create_app(
            _settings(tmp_path),
            model_registry=ProfileRegistry(path),
            model_transport=ApiTransport(),
        )
    )


def test_model_catalog_contains_profiles_but_no_parallel_provider_surface(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)

    assert client.get("/models/catalog").status_code == 401
    response = client.get("/models/catalog", headers=HEADERS)

    assert response.status_code == 200
    assert len(response.json()["profiles"]) == 5
    assert "providers" not in response.json()
    assert len(client.get("/models/profiles", headers=HEADERS).json()) == 5
    assert client.get("/models/providers", headers=HEADERS).status_code == 404
    assert client.put("/models/providers/openai", headers=HEADERS).status_code == 404


def test_complete_uses_task_profile_through_injected_bridge(tmp_path: Path) -> None:
    client = _client(tmp_path)

    completed = client.post(
        "/models/complete",
        headers=HEADERS,
        json={
            "profile_id": "writer",
            "task_type": "writer",
            "messages": [{"role": "user", "content": "test"}],
            "output_schema": None,
            "max_output_tokens": 64,
            "timeout_seconds": 5,
            "temperature": None,
        },
    )

    assert completed.status_code == 200
    assert completed.json()["content"] == "ready"
    assert completed.json()["provider_id"] == "openai"
    assert completed.json()["usage"]["total_tokens"] == 2


def test_missing_desktop_bridge_uses_stable_configuration_error(tmp_path: Path) -> None:
    client = TestClient(create_app(_settings(tmp_path)))

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
    assert "Story Engine model runtime" in response.json()["detail"]["message"]
