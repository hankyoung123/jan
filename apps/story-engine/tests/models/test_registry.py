import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from story_engine.models.contracts import AgentProfile
from story_engine.models.errors import ModelConfigurationError
from story_engine.models.registry import ProfileRegistry, default_registry


def test_agent_profile_requires_provider_qualified_model_reference() -> None:
    with pytest.raises(ValidationError, match="model"):
        AgentProfile(
            name="Writer",
            agent_type="writer",
            default_system_prompt="Write grounded prose.",
            model="duplicate-model-id",
        )


def test_disabled_reasoning_effort_normalizes_to_none() -> None:
    profile = AgentProfile(
        name="Editor",
        agent_type="editor",
        default_system_prompt="Check facts.",
        model="provider/model",
        reasoning_effort="disabled",
    )

    assert profile.reasoning_effort == "none"


def test_default_registry_has_exactly_six_agent_types(tmp_path: Path) -> None:
    registry = ProfileRegistry(tmp_path / "agent-registry.json")

    state = registry.load()

    assert {profile.agent_type for profile in state.profiles} == {
        "actor",
        "game_master",
        "writer",
        "editor",
        "wiki_maintainer",
        "submission_editor",
    }
    assert all(profile.default_system_prompt for profile in state.profiles)
    assert all(profile.model is None for profile in state.profiles)
    assert not registry.path.exists()


def test_registry_replaces_one_fixed_agent_atomically(tmp_path: Path) -> None:
    path = tmp_path / "config" / "agent-registry.json"
    registry = ProfileRegistry(path)
    writer = registry.get_profile("writer").model_copy(
        update={"model": "test-provider/test-cloud-model"}
    )

    registry.upsert_profile(writer)

    assert ProfileRegistry(path).get_profile("writer") == writer
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 5
    assert len(payload["profiles"]) == 6
    assert "providers" not in payload
    assert not list(path.parent.glob("*.tmp"))


def test_registry_rejects_missing_fixed_agent(tmp_path: Path) -> None:
    path = tmp_path / "agent-registry.json"
    payload = default_registry().model_dump(mode="json")
    payload["profiles"] = payload["profiles"][:-1]
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ModelConfigurationError, match="registry is invalid"):
        ProfileRegistry(path).load()


def test_old_registry_is_rejected_without_migration(tmp_path: Path) -> None:
    path = tmp_path / "agent-registry.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 4,
                "profiles": [
                    {
                        "id": "writer",
                        "task_type": "writer",
                        "model_ref": "provider/model",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ModelConfigurationError, match="registry is invalid"):
        ProfileRegistry(path).load()
