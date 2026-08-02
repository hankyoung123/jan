import json
from pathlib import Path

import pytest

from story_engine.models.contracts import ModelProfile
from story_engine.models.errors import ModelConfigurationError
from story_engine.models.registry import ProfileRegistry


def test_default_registry_has_each_required_task_profile(tmp_path: Path) -> None:
    registry = ProfileRegistry(tmp_path / "model-registry.json")

    state = registry.load()

    assert {profile.task_type for profile in state.profiles} == {
        "actor",
        "game_master",
        "reflection",
        "memory_consolidation",
        "projection",
        "editor",
        "writer",
        "embedding",
    }
    assert {profile.provider_id for profile in state.profiles} <= {
        "llamacpp",
        "openai",
    }
    assert not registry.path.exists()


def test_registry_persists_only_task_profiles_atomically(tmp_path: Path) -> None:
    path = tmp_path / "config" / "model-registry.json"
    registry = ProfileRegistry(path)
    profile = ModelProfile(
        id="test-writer",
        name="Test Writer",
        task_type="writer",
        provider_id="custom-provider",
        model="test-model",
    )

    registry.upsert_profile(profile)

    assert ProfileRegistry(path).get_profile(profile.id) == profile
    serialized = path.read_text(encoding="utf-8")
    payload = json.loads(serialized)
    assert payload["schema_version"] == 3
    assert "providers" not in payload
    assert "base_url" not in serialized
    assert "api_key" not in serialized
    assert not list(path.parent.glob("*.tmp"))


def test_old_registry_schema_is_rejected_without_legacy_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model-registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "providers": [
                    {
                        "id": "remote-openai",
                        "name": "Duplicate Provider",
                        "kind": "remote",
                        "base_url": "https://models.example/v1",
                        "requires_api_key": True,
                    }
                ],
                "profiles": [
                    {
                        "id": "writer",
                        "name": "Writer",
                        "task_type": "writer",
                        "provider_id": "openai",
                        "model": "gpt-test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ModelConfigurationError, match="registry is invalid"):
        ProfileRegistry(path).load()


def test_current_profiles_default_reasoning_effort_to_disabled(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model-registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "profiles": [
                    {
                        "id": "game-master",
                        "name": "Game Master",
                        "task_type": "game_master",
                        "provider_id": "deepseek",
                        "model": "deepseek-v4-flash",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    profile = ProfileRegistry(path).load().profiles[0]

    assert profile.reasoning_effort == "disabled"
