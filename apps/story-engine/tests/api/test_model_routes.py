from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.models.contracts import ModelProfile, ModelStreamChunk
from story_engine.models.registry import ProfileRegistry
from story_engine.submission.service import SubmissionService, fog_harbor_submission

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
    registry = ProfileRegistry(path)
    registry.upsert_profile(
        ModelProfile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
        )
    )
    return TestClient(
        create_app(
            _settings(tmp_path),
            model_registry=registry,
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
    assert completed.json()["model_ref"] == "test-provider/test-writer"
    assert completed.json()["usage"]["total_tokens"] == 2


def test_project_model_policy_is_authenticated_and_persists_agent_overrides(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    SubmissionService(settings.projects_root).finalize(fog_harbor_submission())
    client = _client(tmp_path)
    profile = {
        "id": "actor-secondary",
        "task_type": "actor",
        "model_ref": "test-provider/test-actor-secondary",
        "max_output_tokens": 2048,
        "timeout_seconds": 60,
        "temperature": 0.4,
    }
    assert client.put(
        "/models/profiles/actor-secondary",
        headers=HEADERS,
        json=profile,
    ).status_code == 200

    path = "/projects/fog-harbor/model-policy"
    assert client.get(path).status_code == 401
    default = client.get(path, headers=HEADERS)
    assert default.status_code == 200
    policy = default.json()
    assert policy["task_profile_ids"]["game_master"] == "game-master"
    assert policy["agent_profile_ids"] == {}

    policy["agent_profile_ids"] = {"chen-mo": "actor-secondary"}
    saved = client.put(path, headers=HEADERS, json=policy)

    assert saved.status_code == 200
    assert saved.json()["agent_profile_ids"] == {"chen-mo": "actor-secondary"}
    assert client.get(path, headers=HEADERS).json() == saved.json()


def test_project_model_policy_rejects_invalid_project_and_profile_task(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    SubmissionService(settings.projects_root).finalize(fog_harbor_submission())
    client = _client(tmp_path)

    missing = client.get("/projects/missing/model-policy", headers=HEADERS)
    assert missing.status_code == 404

    policy = client.get(
        "/projects/fog-harbor/model-policy",
        headers=HEADERS,
    ).json()
    policy["agent_profile_ids"] = {"chen-mo": "writer"}
    invalid = client.put(
        "/projects/fog-harbor/model-policy",
        headers=HEADERS,
        json=policy,
    )

    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "model_configuration_error"


def test_missing_desktop_bridge_uses_stable_configuration_error(tmp_path: Path) -> None:
    registry = ProfileRegistry(tmp_path / "config" / "models.json")
    registry.upsert_profile(
        ModelProfile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
        )
    )
    client = TestClient(create_app(_settings(tmp_path), model_registry=registry))

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
