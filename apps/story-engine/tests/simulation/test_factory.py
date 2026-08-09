from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from profile_factory import agent_profile as _profile

from story_engine.concordia_runtime.memory import concordia_hash_embedder
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.projection import (
    EffectOperation,
    EffectTarget,
    EventVisibility,
    ResolvedEvent,
    ResolvedTurn,
    StateEffect,
)
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    TurnSessionRequest,
)
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.gateway import (
    ModelGateway,
    ModelPartSink,
    UnavailableModelTransport,
)
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


def test_project_runtime_uses_fixed_agent_assignments(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    registry = ProfileRegistry(tmp_path / "models.json")
    for profile in (
        _profile(
            id="actor",
            task_type="actor",
            model_ref="provider-a/shared-model",
        ),
        _profile(
            id="game_master",
            task_type="game_master",
            model_ref="provider-a/game-master",
        ),
    ):
        registry.upsert_profile(profile)
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
    factory("session:profiles", request)

    from story_engine.simulation.engine import StoryTurnEngine

    engine = StoryTurnEngine(factory)
    snapshot = engine.create_session(request)
    restored = factory.from_snapshot(snapshot.session_id, request, snapshot)
    assert restored.roster_actor_ids() == snapshot.roster_actor_ids


class RecordingTransport:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        first_content_timeout_seconds: float | None = None,
        part_sink: ModelPartSink | None = None,
    ) -> Mapping[str, Any]:
        del timeout_seconds, first_content_timeout_seconds
        self.calls.append(dict(payload))
        if part_sink is not None:
            part_sink("text", "ok")
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


def test_character_goal_update_is_used_by_the_next_concordia_actor_action(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="actor",
            task_type="actor",
            model_ref="provider-a/shared-model",
        )
    )
    transport = RecordingTransport()
    runtime = ProjectRuntimeFactory(
        tmp_path,
        ModelGateway(registry, transport),
    )(
        "session:actor-state",
        TurnSessionRequest(
            project_id="fog-harbor",
            branch_id="main",
            premise_text="灯塔突然熄灭。",
            actor_ids=("chen-mo",),
            content_locale="zh-CN",
            control=ControlPolicy(mode=ControlMode.STEP),
        ),
    )
    new_goal = "立刻去码头核对最后一班船的乘客名单"
    update = StateEffect(
        effect_id="effect:goal:chen-mo",
        operation=EffectOperation.SET,
        target=EffectTarget.CHARACTER_PROJECTION,
        target_id="chen-mo",
        path="current_goal",
        after=new_goal,
    )
    event = ResolvedEvent(
        event_id="event:session:actor-state:0",
        session_id="session:actor-state",
        step=0,
        actor_id="chen-mo",
        event_text="陈默改变了计划。",
        visibility=EventVisibility.PARTICIPANTS,
        participant_ids=("chen-mo",),
        effects=(update,),
        content_locale="zh-CN",
        occurred_at=datetime.now(UTC),
    )
    runtime._apply_character_effects(
        ResolvedTurn(
            session_id="session:actor-state",
            branch_id="main",
            step=0,
            acting_actor_id="chen-mo",
            putative_event_text="我改变计划。",
            raw_resolution_text=event.event_text,
            events=(event,),
            effects=(update,),
            content_locale="zh-CN",
        )
    )

    runtime._all_actors_by_name["chen-mo"].act(
        ActionSpec(
            spec_id="action:session:actor-state:1",
            output_type=ActionOutputType.FREE,
            call_to_action="What do you do next?",
            content_locale="zh-CN",
        )
    )

    prompt = "\n".join(
        message["content"] for message in transport.calls[-1]["messages"]
    )
    assert new_goal in prompt


def test_actor_profile_is_reloaded_per_call(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="actor",
            task_type="actor",
            model_ref="provider-a/shared-model",
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
    registry.upsert_profile(
        _profile(
            id="actor",
            task_type="actor",
            model_ref="provider-b/shared-model",
        )
    )
    actor_model.sample_text("Probe again.", terminators=())

    assert transport.calls[0]["model"] == "provider-a/shared-model"
    assert transport.calls[1]["model"] == "provider-b/shared-model"
