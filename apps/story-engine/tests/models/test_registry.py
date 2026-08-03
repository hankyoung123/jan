import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from story_engine.models.contracts import ModelProfile
from story_engine.models.errors import ModelConfigurationError
from story_engine.models.registry import ProfileRegistry


def test_model_profile_requires_provider_qualified_model_reference() -> None:
    with pytest.raises(ValidationError, match="model_ref"):
        ModelProfile(
            id="writer",
            task_type="writer",
            model_ref="duplicate-model-id",
        )


def test_default_registry_has_each_required_task_profile(tmp_path: Path) -> None:
    registry = ProfileRegistry(tmp_path / "model-registry.json")

    state = registry.load()

    assert {profile.task_type for profile in state.profiles} == {
        "actor",
        "game_master",
        "wiki_maintenance",
        "editor",
        "writer",
    }
    assert all(profile.model_ref is None for profile in state.profiles)
    assert not registry.path.exists()


def test_registry_persists_only_task_profiles_atomically(tmp_path: Path) -> None:
    path = tmp_path / "config" / "model-registry.json"
    registry = ProfileRegistry(path)
    profile = ModelProfile(
        id="test-writer",
        task_type="writer",
        model_ref="test-provider/test-cloud-model",
    )

    registry.upsert_profile(profile)

    assert ProfileRegistry(path).get_profile(profile.id) == profile
    serialized = path.read_text(encoding="utf-8")
    payload = json.loads(serialized)
    assert payload["schema_version"] == 4
    assert "providers" not in payload
    assert "base_url" not in serialized
    assert "api_key" not in serialized
    assert not list(path.parent.glob("*.tmp"))


def test_schema_three_registry_is_migrated_with_recoverable_backup(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model-registry.json"
    legacy_payload = {
        "schema_version": 3,
        "profiles": [
            {
                "id": "actor",
                "name": "Actor",
                "task_type": "actor",
                "provider_id": "deepseek",
                "model": "deepseek-v4-flash",
                "max_output_tokens": 2048,
                "timeout_seconds": 60,
                "temperature": 0.7,
                "reasoning_effort": "disabled",
                "enabled": True,
            },
            {
                "id": "projection",
                "name": "Projection",
                "task_type": "projection",
                "provider_id": "openai",
                "model": "gpt-5-mini",
                "max_output_tokens": 4096,
                "timeout_seconds": 120,
                "temperature": 0.1,
                "reasoning_effort": "disabled",
                "enabled": True,
            },
            {
                "id": "embedding",
                "name": "Embedding",
                "task_type": "embedding",
                "provider_id": "local",
                "model": "bge-m3",
                "max_output_tokens": 2048,
                "timeout_seconds": 60,
                "temperature": None,
                "reasoning_effort": "disabled",
                "enabled": True,
            },
        ],
    }
    path.write_text(json.dumps(legacy_payload), encoding="utf-8")

    state = ProfileRegistry(path).load()

    assert state.schema_version == 4
    assert ProfileRegistry(path).get_profile("actor").model_ref == (
        "deepseek/deepseek-v4-flash"
    )
    assert ProfileRegistry(path).get_profile("wiki-maintenance").model_ref == (
        "openai/gpt-5-mini"
    )
    assert {profile.task_type for profile in state.profiles} == {
        "actor",
        "game_master",
        "wiki_maintenance",
        "editor",
        "writer",
    }
    backup = path.with_suffix(".json.schema-3.bak")
    assert json.loads(backup.read_text(encoding="utf-8")) == legacy_payload
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 4


def test_unsupported_registry_schema_is_rejected(
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


def test_current_profiles_may_be_unconfigured_until_the_user_selects_a_model(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model-registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "profiles": [
                    {
                        "id": "game-master",
                        "task_type": "game_master",
                        "model_ref": None,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    profile = ProfileRegistry(path).load().profiles[0]

    assert profile.model_ref is None
