from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from story_engine.concordia_runtime.memory import concordia_hash_embedder
from story_engine.domain.session_manifest import SessionManifest
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    TurnSessionRequest,
)
from story_engine.models.contracts import ModelProfile, ModelStreamChunk
from story_engine.models.gateway import ModelGateway, UnavailableModelTransport
from story_engine.models.policy import ProjectModelPolicyStore
from story_engine.models.registry import ProfileRegistry
from story_engine.simulation.factory import ProjectRuntimeFactory
from story_engine.submission.service import SubmissionService, fog_harbor_submission


def test_project_runtime_imports_seed_with_private_memory_isolation(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    gateway = ModelGateway(
        ProfileRegistry(tmp_path / "models.json"),
        UnavailableModelTransport(),
    )
    runtime = ProjectRuntimeFactory(
        tmp_path,
        gateway,
        embedder=concordia_hash_embedder,
    )(
        "session:1",
        TurnSessionRequest(
            project_id="fog-harbor",
            branch_id="main",
            premise_text="灯塔突然熄灭。",
            content_locale="zh-CN",
            control=ControlPolicy(mode=ControlMode.STEP),
        ),
    )
    actor_memories = {
        actor.name: " ".join(
            record.text for record in actor.memory.retrieve_recent(limit=20)
        )
        for actor in runtime.actors
    }
    gm_memories = " ".join(
        record.text for record in runtime.game_master.memory.retrieve_recent(limit=20)
    )

    assert "父亲在灯塔附近失踪" in actor_memories["chen-mo"]
    assert "未归档的值班表" not in actor_memories["chen-mo"]
    assert "未归档的值班表" in actor_memories["lin-lan"]
    assert "父亲在灯塔附近失踪" not in actor_memories["lin-lan"]
    assert "父亲在灯塔附近失踪" in gm_memories
    assert "未归档的值班表" in gm_memories


def test_project_runtime_resolves_and_restores_project_model_assignments(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    registry = ProfileRegistry(tmp_path / "models.json")
    for profile in (
        ModelProfile(
            id="actor-dramatic",
            task_type="actor",
            model_ref="provider-a/shared-model",
        ),
        ModelProfile(
            id="actor-precise",
            task_type="actor",
            model_ref="provider-b/shared-model",
        ),
        ModelProfile(
            id="gm-project",
            task_type="game_master",
            model_ref="provider-a/game-master",
        ),
    ):
        registry.upsert_profile(profile)
    policy_store = ProjectModelPolicyStore(tmp_path / "fog-harbor", registry)
    policy = policy_store.load()
    policy_store.save(
        policy.model_copy(
            update={
                "task_profile_ids": {
                    **policy.task_profile_ids,
                    "game_master": "gm-project",
                },
                "agent_profile_ids": {
                    "chen-mo": "actor-dramatic",
                    "lin-lan": "actor-precise",
                },
            }
        )
    )
    gateway = ModelGateway(registry, UnavailableModelTransport())
    factory = ProjectRuntimeFactory(tmp_path, gateway)
    request = TurnSessionRequest(
        project_id="fog-harbor",
        branch_id="main",
        premise_text="灯塔突然熄灭。",
        actor_ids=("chen-mo", "lin-lan"),
        content_locale="zh-CN",
        control=ControlPolicy(mode=ControlMode.STEP),
    )
    runtime = factory("session:profiles", request)

    assert runtime.resolved_model_profile_ids() == {
        "task:actor": "actor",
        "task:game_master": "gm-project",
        "task:wiki_maintenance": "wiki-maintenance",
        "task:editor": "editor",
        "task:writer": "writer",
        "agent:chen-mo": "actor-dramatic",
        "agent:lin-lan": "actor-precise",
    }

    from story_engine.simulation.engine import StoryTurnEngine

    engine = StoryTurnEngine(factory)
    snapshot = engine.create_session(request)
    manifest = SessionManifest.from_snapshot(snapshot)
    assert manifest.resolved_model_profile_ids == snapshot.resolved_model_profile_ids

    changed = policy_store.load()
    policy_store.save(
        changed.model_copy(
            update={"agent_profile_ids": {"chen-mo": "actor-precise"}}
        )
    )
    restored = factory.from_snapshot(snapshot.session_id, request, snapshot)
    assert restored.resolved_model_profile_ids() == snapshot.resolved_model_profile_ids
    assert restored.resolved_model_profile_ids()["agent:chen-mo"] == "actor-dramatic"


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(dict(payload))
        return {
            "choices": [
                {
                    "message": {"content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[ModelStreamChunk]:
        del payload, timeout_seconds
        if False:
            yield ModelStreamChunk()
        raise AssertionError("factory live mapping tests do not stream")


def test_actor_profile_mapping_is_resolved_per_call_after_policy_change(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    registry = ProfileRegistry(tmp_path / "models.json")
    for profile in (
        ModelProfile(
            id="actor-dramatic",
            task_type="actor",
            model_ref="provider-a/shared-model",
        ),
        ModelProfile(
            id="actor-precise",
            task_type="actor",
            model_ref="provider-b/shared-model",
        ),
    ):
        registry.upsert_profile(profile)
    policy_store = ProjectModelPolicyStore(tmp_path / "fog-harbor", registry)
    policy_store.save(
        policy_store.load().model_copy(
            update={"agent_profile_ids": {"chen-mo": "actor-dramatic"}}
        )
    )
    transport = RecordingTransport()
    gateway = ModelGateway(registry, transport)
    factory = ProjectRuntimeFactory(tmp_path, gateway)
    runtime = factory(
        "session:live",
        TurnSessionRequest(
            project_id="fog-harbor",
            branch_id="main",
            premise_text="灯塔突然熄灭。",
            actor_ids=("chen-mo",),
            content_locale="zh-CN",
            control=ControlPolicy(mode=ControlMode.STEP),
        ),
    )
    actor_model = next(
        model
        for model in runtime._language_models
        if getattr(model, "_actor_id", None) == "chen-mo"
    )

    actor_model.sample_text("Probe.", terminators=())
    policy_store.save(
        policy_store.load().model_copy(
            update={"agent_profile_ids": {"chen-mo": "actor-precise"}}
        )
    )
    actor_model.sample_text("Probe again.", terminators=())

    assert transport.calls[0]["model"] == "provider-a/shared-model"
    assert transport.calls[1]["model"] == "provider-b/shared-model"
