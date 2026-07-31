from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.models.contracts import ModelStreamChunk, ProviderConfig
from story_engine.models.registry import ProfileRegistry
from story_engine.models.secrets import MemorySecretStore

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


class ApiTransport:
    async def complete(
        self,
        provider: ProviderConfig,
        payload: Mapping[str, Any],
        *,
        credential: str | None,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        return {
            "choices": [{"message": {"content": "ready"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    async def stream(
        self,
        provider: ProviderConfig,
        payload: Mapping[str, Any],
        *,
        credential: str | None,
        timeout_seconds: int,
    ) -> AsyncIterator[ModelStreamChunk]:
        yield ModelStreamChunk(delta="ready")
        yield ModelStreamChunk(done=True)


def _client(tmp_path: Path) -> tuple[TestClient, MemorySecretStore]:
    secrets = MemorySecretStore()
    app = create_app(
        EngineSettings(
            session_token=TOKEN,
            projects_root=tmp_path / "projects",
            model_registry_path=tmp_path / "config" / "models.json",
        ),
        model_registry=ProfileRegistry(tmp_path / "config" / "models.json"),
        secret_store=secrets,
        model_transport=ApiTransport(),
    )
    return TestClient(app), secrets


def test_model_catalog_requires_session_auth_and_never_returns_secrets(
    tmp_path: Path,
) -> None:
    client, secrets = _client(tmp_path)
    secrets.set("remote-openai", "never-return-this-key")

    assert client.get("/models/catalog").status_code == 401
    response = client.get("/models/catalog", headers=HEADERS)

    assert response.status_code == 200
    assert len(response.json()["profiles"]) == 5
    serialized = response.text
    assert "never-return-this-key" not in serialized
    remote = next(
        item for item in response.json()["providers"] if item["id"] == "remote-openai"
    )
    assert remote["has_api_key"] is True
    assert len(client.get("/models/profiles", headers=HEADERS).json()) == 5
    assert len(client.get("/models/providers", headers=HEADERS).json()) == 2


def test_provider_key_is_write_only_and_complete_uses_profile(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    provider = client.put(
        "/models/providers/remote-openai",
        headers=HEADERS,
        json={
            "name": "Remote",
            "kind": "remote",
            "base_url": "https://models.example/v1",
            "requires_api_key": True,
            "api_key": "write-only-key",
        },
    )

    assert provider.status_code == 200
    assert provider.json()["has_api_key"] is True
    assert "write-only-key" not in provider.text

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
    assert completed.json()["usage"]["total_tokens"] == 2


def test_model_errors_use_stable_codes(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

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
    assert response.json()["detail"]["code"] == "missing_credential"
