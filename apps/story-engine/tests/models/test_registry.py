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


def test_legacy_disabled_reasoning_effort_normalizes_to_none() -> None:
    profile = ModelProfile(
        id="editor",
        task_type="editor",
        model_ref="provider/model",
        reasoning_effort="disabled",
    )

    assert profile.reasoning_effort == "none"
    assert profile.model_dump(mode="json")["reasoning_effort"] == "none"


def test_saved_disabled_reasoning_effort_loads_without_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model-registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "profiles": [
                    {
                        "id": "editor",
                        "task_type": "editor",
                        "model_ref": "provider/model",
                        "reasoning_effort": "disabled",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    profile = ProfileRegistry(path).load().profiles[0]

    assert profile.reasoning_effort == "none"


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


def test_schema_three_registry_is_rejected_without_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "model-registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "profiles": [
                    {
                        "id": "actor",
                        "task_type": "actor",
                        "provider_id": "deepseek",
                        "model": "deepseek-v4-flash",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ModelConfigurationError, match="registry is invalid"):
        ProfileRegistry(path).load()

    assert not path.with_suffix(".json.schema-3.bak").exists()


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
