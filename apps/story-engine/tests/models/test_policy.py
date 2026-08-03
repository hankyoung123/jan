import json
from pathlib import Path

import pytest

from story_engine.domain.model_policy import ProjectModelPolicy
from story_engine.models.contracts import ModelProfile
from story_engine.models.errors import ModelConfigurationError
from story_engine.models.policy import ProjectModelPolicyStore
from story_engine.models.registry import ProfileRegistry
from story_engine.submission.service import SubmissionService, fog_harbor_submission


def _configured_registry(tmp_path: Path) -> ProfileRegistry:
    registry = ProfileRegistry(tmp_path / "config" / "model-registry.json")
    registry.upsert_profile(
        ModelProfile(
            id="actor-dramatic",
            task_type="actor",
            model_ref="provider-a/shared-model",
        )
    )
    registry.upsert_profile(
        ModelProfile(
            id="actor-precise",
            task_type="actor",
            model_ref="provider-b/shared-model",
        )
    )
    return registry


def test_default_policy_binds_every_task_to_the_default_profile(tmp_path: Path) -> None:
    project_root = tmp_path / "projects" / "fog-harbor"
    SubmissionService(tmp_path / "projects").finalize(fog_harbor_submission())
    store = ProjectModelPolicyStore(project_root, _configured_registry(tmp_path))

    policy = store.load()

    assert policy.task_profile_ids == {
        "actor": "actor",
        "game_master": "game-master",
        "wiki_maintenance": "wiki-maintenance",
        "editor": "editor",
        "writer": "writer",
    }
    assert policy.agent_profile_ids == {}
    assert not store.path.exists()


def test_policy_persists_per_character_overrides_atomically(tmp_path: Path) -> None:
    project_root = tmp_path / "projects" / "fog-harbor"
    SubmissionService(tmp_path / "projects").finalize(fog_harbor_submission())
    registry = _configured_registry(tmp_path)
    store = ProjectModelPolicyStore(project_root, registry)
    policy = store.load().model_copy(
        update={
            "agent_profile_ids": {
                "chen-mo": "actor-dramatic",
                "lin-lan": "actor-precise",
            }
        }
    )

    saved = store.save(policy)

    assert ProjectModelPolicyStore(project_root, registry).load() == saved
    assert json.loads(store.path.read_text(encoding="utf-8")) == (
        policy.model_dump(mode="json")
    )
    assert not list(store.path.parent.glob("*.tmp"))


def test_policy_rejects_unknown_character_override(tmp_path: Path) -> None:
    project_root = tmp_path / "projects" / "fog-harbor"
    SubmissionService(tmp_path / "projects").finalize(fog_harbor_submission())
    store = ProjectModelPolicyStore(project_root, _configured_registry(tmp_path))
    policy = store.load().model_copy(
        update={"agent_profile_ids": {"unknown-agent": "actor-dramatic"}}
    )

    with pytest.raises(ModelConfigurationError, match="unknown character"):
        store.save(policy)


def test_policy_rejects_missing_and_task_mismatched_profiles(tmp_path: Path) -> None:
    project_root = tmp_path / "projects" / "fog-harbor"
    SubmissionService(tmp_path / "projects").finalize(fog_harbor_submission())
    store = ProjectModelPolicyStore(project_root, _configured_registry(tmp_path))

    with pytest.raises(ModelConfigurationError, match="does not exist"):
        store.save(
            store.load().model_copy(
                update={"agent_profile_ids": {"chen-mo": "missing-actor"}}
            )
        )

    with pytest.raises(ModelConfigurationError, match="requires task 'actor'"):
        store.save(
            store.load().model_copy(
                update={"agent_profile_ids": {"chen-mo": "writer"}}
            )
        )

    with pytest.raises(ModelConfigurationError, match="requires task 'writer'"):
        store.save(
            store.load().model_copy(
                update={
                    "task_profile_ids": {
                        **store.load().task_profile_ids,
                        "writer": "actor-dramatic",
                    }
                }
            )
        )


def test_policy_is_project_owned_and_branch_independent(tmp_path: Path) -> None:
    project_root = tmp_path / "projects" / "fog-harbor"
    SubmissionService(tmp_path / "projects").finalize(fog_harbor_submission())
    registry = _configured_registry(tmp_path)
    store = ProjectModelPolicyStore(project_root, registry)
    store.save(
        ProjectModelPolicy(
            agent_profile_ids={"chen-mo": "actor-dramatic"},
        )
    )

    assert store.path == project_root / ".story-engine/config/model-policy.json"
    assert not list(
        (project_root / ".story-engine/branches").rglob("model-policy.json")
    )
