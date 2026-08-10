# ruff: noqa: RUF001

import json
import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

from profile_factory import agent_profile as _profile

from story_engine.concordia_runtime.factory import ConcordiaStoryActor
from story_engine.concordia_runtime.prefabs.game_master import _resolve_story_event
from story_engine.concordia_runtime.resolver import ConcordiaResolverKernel
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.memory import MemorySnapshot
from story_engine.domain.models import Character
from story_engine.domain.projection import (
    EventVisibility,
    ResolvedEvent,
    ResolvedTurn,
    SimulationBoundary,
)
from story_engine.domain.recipe import PerceptionFrame
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    ResolverContext,
    TurnSessionRequest,
)
from story_engine.events.stream import EngineEventBus
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.gateway import ModelGateway, ModelPartSink
from story_engine.models.registry import ProfileRegistry
from story_engine.simulation.command_service import SimulationCommandService
from story_engine.simulation.commands import SessionCommandCoordinator
from story_engine.simulation.engine import StoryTurnEngine
from story_engine.simulation.factory import ProjectRuntimeFactory
from story_engine.simulation.persistence import SimulationPersistenceService
from story_engine.simulation.projection_coordinator import (
    SimulationProjectionCoordinator,
)
from story_engine.simulation.runtime import StorySimulationRuntime
from story_engine.submission.service import (
    SubmissionService,
    last_ferry_before_submission,
)


class ConcordiaHandoffTransport:
    """Deterministic provider boundary for a real Concordia handoff."""

    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []
        self.observation_prompts: list[str] = []
        self.resolution_count = 0

    @staticmethod
    def _properties(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        response_format = payload.get("response_format")
        if not isinstance(response_format, Mapping):
            return {}
        json_schema = response_format.get("json_schema")
        if not isinstance(json_schema, Mapping):
            return {}
        schema = json_schema.get("schema")
        if not isinstance(schema, Mapping):
            return {}
        properties = schema.get("properties")
        return properties if isinstance(properties, Mapping) else {}

    @staticmethod
    def _choice(
        prompt: str,
        properties: Mapping[str, Any],
        semantic: str,
    ) -> str:
        choice = properties.get("choice")
        candidates = choice.get("enum", ()) if isinstance(choice, Mapping) else ()
        for candidate in candidates:
            value = str(candidate)
            if value == semantic or re.search(
                rf"\({re.escape(value)}\)\s+{re.escape(semantic)}(?:\n|$)",
                prompt,
            ):
                return value
        raise AssertionError(f"no choice maps to {semantic!r}: {prompt}")

    def _content(self, payload: Mapping[str, Any], prompt: str) -> str:
        properties = self._properties(payload)
        if "event_text" in properties:
            self.resolution_count += 1
            is_player = self.resolution_count == 1
            return json.dumps(
                {
                    "event_text": (
                        "林澈听到了玩家的问题。"
                        if is_player
                        else "林澈选择保持沉默，大厅里的气氛变得紧张。"
                    ),
                    "boundary": "chapter" if is_player else "scene",
                    "visibility": "participants",
                    "observer_names": [],
                    "participant_names": ["你", "林澈"],
                    "entity_changes": [],
                    "state_updates": [],
                },
                ensure_ascii=False,
            )
        if "output_type" in properties:
            return json.dumps(
                {
                    "call_to_action": "决定是否回答玩家的问题。",
                    "output_type": "free",
                    "options": [],
                    "tag": "dialogue",
                },
                ensure_ascii=False,
            )
        if "actor_names" in properties:
            actor_names = properties["actor_names"]
            items = (
                actor_names.get("items", {})
                if isinstance(actor_names, Mapping)
                else {}
            )
            candidates = items.get("enum", ()) if isinstance(items, Mapping) else ()
            return json.dumps({"actor_names": list(candidates)}, ensure_ascii=False)
        if "choice" in properties:
            semantic = "林澈" if "Whose turn is next" in prompt else "No"
            return json.dumps(
                {"choice": self._choice(prompt, properties, semantic)},
                ensure_ascii=False,
            )
        if payload.get("model") == "test-provider/actor":
            return "我选择保持沉默。"
        self.observation_prompts.append(prompt)
        return "林澈听见问题后仍站在玩家面前。"

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
        messages = payload["messages"]
        prompt = "\n".join(str(message["content"]) for message in messages)
        content = self._content(payload, prompt)
        if part_sink is not None:
            part_sink("text", content)
        return {
            "choices": [
                {
                    "message": {"content": content},
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
        raise AssertionError("NPC handoff integration does not stream")


class RecordingActor:
    def __init__(
        self,
        actor_id: str,
        display_name: str,
        intent: str,
        log: list[str],
    ) -> None:
        self.name = actor_id
        self.display_name = display_name
        self.intent = intent
        self.log = log
        self.act_calls = 0
        self._state: dict[str, object] = {}

    def act(self, _action_spec: ActionSpec) -> str:
        self.act_calls += 1
        self.log.append(f"act:{self.name}")
        return self.intent

    def observe(self, _perception: PerceptionFrame) -> None:
        return None

    def get_state(self) -> dict[str, object]:
        return dict(self._state)

    def set_state(self, state: dict[str, object]) -> None:
        self._state = dict(state)

    def set_actor_state(self, state: str) -> None:
        self._state["actor_state"] = state


class RecordingGameMaster:
    name = "gm"

    def __init__(
        self,
        log: list[str],
        *,
        terminate_after_resolution: bool = False,
    ) -> None:
        self.log = log
        self.selected_candidates: list[tuple[str, ...]] = []
        self.observed_actor_ids: list[str] = []
        self.terminate_after_resolution = terminate_after_resolution
        self._state: dict[str, object] = {}

    def set_active_actor(self, _display_name: str) -> None:
        return None

    def should_terminate(self, **_kwargs: object) -> tuple[bool, None]:
        self.log.append("terminate")
        return self.terminate_after_resolution, None

    def make_observation(
        self,
        actor: RecordingActor,
        **kwargs: object,
    ) -> PerceptionFrame:
        self.observed_actor_ids.append(actor.name)
        return PerceptionFrame(
            frame_id=f"observation:{actor.name}",
            session_id=str(kwargs["session_id"]),
            branch_id="main",
            actor_id=actor.name,
            step=int(kwargs["step"]),
            content_locale=str(kwargs["content_locale"]),
            observation_text="The lobby remains tense.",
        )

    def select_next_actor(
        self,
        actors: tuple[RecordingActor, ...],
        **_kwargs: object,
    ) -> str:
        candidates = tuple(actor.name for actor in actors)
        self.selected_candidates.append(candidates)
        return candidates[0]

    def create_action_spec(
        self,
        actor: RecordingActor,
        **kwargs: object,
    ) -> ActionSpec:
        return ActionSpec(
            spec_id=f"action:{kwargs['session_id']}:{kwargs['step']}:{actor.name}",
            output_type=ActionOutputType.FREE,
            call_to_action="Decide your own response.",
            content_locale=str(kwargs["content_locale"]),
        )

    def get_state(self) -> dict[str, object]:
        return dict(self._state)

    def set_state(self, state: dict[str, object]) -> None:
        self._state = dict(state)


class RecordingResolver:
    def __init__(
        self,
        log: list[str],
        *,
        player_boundary: SimulationBoundary = SimulationBoundary.NONE,
        player_participants: tuple[str, ...] = ("player", "lin-che"),
    ) -> None:
        self.log = log
        self.player_boundary = player_boundary
        self.player_participants = player_participants
        self.contexts: list[ResolverContext] = []

    def resolve(
        self,
        _game_master: RecordingGameMaster,
        context: ResolverContext,
        *,
        cancellation: Event,
    ) -> ResolvedTurn:
        assert not cancellation.is_set()
        self.contexts.append(context)
        self.log.append(f"resolve:{context.acting_actor_id}")
        is_player = context.acting_actor_id == "player"
        event_text = (
            "林澈听到了玩家的问题。"
            if is_player
            else "林澈的沉默让大厅里的气氛变得紧张。"
        )
        event = ResolvedEvent(
            event_id=f"event:{context.session_id}:{context.step}",
            session_id=context.session_id,
            step=context.step,
            actor_id=context.acting_actor_id,
            event_text=event_text,
            visibility=EventVisibility.PARTICIPANTS,
            participant_ids=(
                self.player_participants
                if is_player
                else ("player", "lin-che")
            ),
            content_locale=context.content_locale,
            occurred_at=datetime.now(UTC),
        )
        return ResolvedTurn(
            session_id=context.session_id,
            branch_id=context.branch_id,
            step=context.step,
            acting_actor_id=context.acting_actor_id,
            putative_event_text=context.putative_event_text,
            raw_resolution_text=event_text,
            events=(event,),
            boundary=(
                self.player_boundary
                if is_player
                else SimulationBoundary.NONE
            ),
            content_locale=context.content_locale,
        )


class RemovingRosterPlanner:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    def select_next(self, *_args: object, **_kwargs: object) -> tuple[()]:
        self.log.append("roster")
        return ()


class RecordingRuntime(StorySimulationRuntime):
    def memory_snapshots(self) -> dict[str, MemorySnapshot]:
        return {}


def _character(actor_id: str, display_name: str) -> Character:
    return Character(
        id=actor_id,
        display_name=display_name,
        type="active",
        identity=f"Identity of {display_name}",
        core_desire=f"Desire of {display_name}",
        current_goal=f"Goal of {display_name}",
    )


def _interactive_service(
    *,
    player_boundary: SimulationBoundary = SimulationBoundary.NONE,
    player_participants: tuple[str, ...] = ("player", "lin-che"),
    remove_roster_at_boundary: bool = False,
    max_scenes: int = 5,
    initial_completed_scenes: int = 0,
    terminate_after_resolution: bool = False,
) -> tuple[
    SimulationCommandService,
    str,
    dict[str, RecordingActor],
    RecordingGameMaster,
    RecordingResolver,
    RecordingRuntime,
    list[str],
]:
    log: list[str] = []
    actors = {
        "player": RecordingActor("player", "玩家", "", log),
        "lin-che": RecordingActor("lin-che", "林澈", "我选择保持沉默。", log),
        "bystander": RecordingActor("bystander", "旁观者", "我离开大厅。", log),
    }
    game_master = RecordingGameMaster(
        log,
        terminate_after_resolution=terminate_after_resolution,
    )
    resolver = RecordingResolver(
        log,
        player_boundary=player_boundary,
        player_participants=player_participants,
    )
    runtime_ref: dict[str, RecordingRuntime] = {}

    def runtime_factory(
        session_id: str,
        request: TurnSessionRequest,
    ) -> RecordingRuntime:
        runtime = RecordingRuntime(
            project_id=request.project_id,
            session_id=session_id,
            branch_id=request.branch_id,
            content_locale=request.content_locale,
            actors=tuple(actors.values()),  # type: ignore[arg-type]
            game_master=game_master,  # type: ignore[arg-type]
            resolver=resolver,  # type: ignore[arg-type]
            characters=tuple(
                _character(actor.name, actor.display_name)
                for actor in actors.values()
            ),
            player_actor_id="player",
            initial_roster_selected=True,
            roster_planner=(
                RemovingRosterPlanner(log)  # type: ignore[arg-type]
                if remove_roster_at_boundary
                else None
            ),
            game_master_rebuilder=lambda _actors, previous: previous,
        )
        if initial_completed_scenes:
            runtime.initial_snapshot = SimpleNamespace(
                current_step=0,
                completed_scenes=initial_completed_scenes,
                raw_log_offset=0,
                total_model_tokens=0,
                consecutive_model_failures=0,
                checkpoint_id=None,
            )
        runtime_ref["runtime"] = runtime
        return runtime

    engine = StoryTurnEngine(runtime_factory)  # type: ignore[arg-type]
    event_bus = EngineEventBus()
    service = SimulationCommandService(
        engine,
        SimulationPersistenceService(engine, event_bus),
        SimulationProjectionCoordinator(event_bus),
        SessionCommandCoordinator(),
    )
    snapshot = engine.create_session(
        TurnSessionRequest(
            project_id="project-1",
            branch_id="main",
            premise_text="A tense conversation in a hotel lobby.",
            actor_ids=tuple(actors),
            player_actor_id="player",
            content_locale="zh-CN",
            control=ControlPolicy(
                mode=ControlMode.STEP,
                max_steps=10,
                max_scenes=max_scenes,
            ),
        )
    )
    return (
        service,
        snapshot.session_id,
        actors,
        game_master,
        resolver,
        runtime_ref["runtime"],
        log,
    )


def test_player_question_calls_the_affected_npc_actor() -> None:
    service, session_id, actors, *_ = _interactive_service()

    service.interactive_turn(session_id, text="林澈，那条消息是不是你发的？")

    assert actors["lin-che"].act_calls == 1


def test_reserved_npc_acts_before_true_termination_decision() -> None:
    service, session_id, actors, _gm, _resolver, _runtime, log = (
        _interactive_service(terminate_after_resolution=True)
    )

    service.interactive_turn(session_id, text="林澈，那条消息是不是你发的？")

    assert actors["lin-che"].act_calls == 1
    assert log.index("act:lin-che") < log.index("terminate")


def test_reserved_npc_resolution_is_committed_before_session_terminates() -> None:
    service, session_id, _actors, _gm, resolver, _runtime, log = (
        _interactive_service(terminate_after_resolution=True)
    )

    result = service.interactive_turn(
        session_id,
        text="林澈，那条消息是不是你发的？",
    )

    snapshot = service.engine.get(session_id)
    assert log.index("resolve:lin-che") < log.index("terminate")
    assert [context.acting_actor_id for context in resolver.contexts] == [
        "player",
        "lin-che",
    ]
    assert result.status.value == "terminated"
    assert snapshot.current_step == 2
    assert snapshot.raw_log_offset == 2


def test_reached_scene_budget_waits_for_required_npc_response() -> None:
    service, session_id, actors, *_ = _interactive_service(
        max_scenes=1,
        initial_completed_scenes=1,
    )

    result = service.interactive_turn(
        session_id,
        text="林澈，那条消息是不是你发的？",
    )

    assert actors["lin-che"].act_calls == 1
    assert result.resolved_turn is not None
    assert result.resolved_turn.events[-1].actor_id == "lin-che"
    assert result.status.value == "terminated"
    assert (
        service.engine.get(session_id).termination_reason_text
        == "maximum scene budget reached"
    )


def test_player_resolution_forbids_generating_npc_voluntary_dialogue() -> None:
    document = SimpleNamespace(
        statement=lambda _text: None,
        open_question=lambda **kwargs: kwargs["question"],
    )

    question = _resolve_story_event(document, "Committed world.", "玩家")

    assert "Voluntary NPC behavior must originate from that NPC Actor." in question
    assert "do not invent voluntary dialogue, decisions" in question
    assert "lies, refusals, cooperation, escape, or new plans" in question


def test_npc_output_returns_as_intent_for_gm_resolution() -> None:
    service, session_id, actors, _gm, resolver, *_ = _interactive_service()

    service.interactive_turn(session_id, text="林澈，那条消息是不是你发的？")

    assert [context.acting_actor_id for context in resolver.contexts] == [
        "player",
        "lin-che",
    ]
    assert resolver.contexts[1].putative_event_text == actors["lin-che"].intent


def test_player_scene_boundary_waits_for_required_npc_resolution() -> None:
    service, session_id, _actors, _gm, _resolver, runtime, log = (
        _interactive_service(
            player_boundary=SimulationBoundary.SCENE,
            remove_roster_at_boundary=True,
        )
    )

    result = service.interactive_turn(
        session_id,
        text="林澈，那条消息是不是你发的？",
    )

    assert log.index("act:lin-che") < log.index("resolve:lin-che") < log.index("roster")
    assert result.boundary == SimulationBoundary.SCENE
    assert runtime.roster_actor_ids() == ("player",)
    assert runtime.pending_scene_events() == ()


def test_unrelated_current_scene_npc_is_not_called() -> None:
    service, session_id, actors, game_master, *_ = _interactive_service()

    service.interactive_turn(session_id, text="林澈，那条消息是不是你发的？")

    assert game_master.selected_candidates == [("lin-che",)]
    assert game_master.observed_actor_ids == ["lin-che"]
    assert actors["lin-che"].act_calls == 1
    assert actors["bystander"].act_calls == 0


def test_real_concordia_gateway_hands_player_intent_to_eligible_npc(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(last_ferry_before_submission())
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="actor",
            task_type="actor",
            model_ref="test-provider/actor",
        )
    )
    registry.upsert_profile(
        _profile(
            id="game_master",
            task_type="game_master",
            model_ref="test-provider/game-master",
        )
    )
    transport = ConcordiaHandoffTransport()
    gateway = ModelGateway(registry, transport)
    runtime_factory = ProjectRuntimeFactory(
        tmp_path,
        gateway,
    )
    runtime_ref: dict[str, StorySimulationRuntime] = {}

    def capture_runtime(
        session_id: str,
        request: TurnSessionRequest,
    ) -> StorySimulationRuntime:
        runtime = runtime_factory(session_id, request)
        runtime_ref["runtime"] = runtime
        return runtime

    engine = StoryTurnEngine(capture_runtime)
    event_bus = EngineEventBus()
    service = SimulationCommandService(
        engine,
        SimulationPersistenceService(engine, event_bus),
        SimulationProjectionCoordinator(event_bus),
        SessionCommandCoordinator(),
    )
    snapshot = engine.create_session(
        TurnSessionRequest(
            project_id="last-ferry-before",
            branch_id="main",
            premise_text="玩家当面询问林澈消息的来源。",
            actor_ids=("player", "lin-che", "zhang-ye"),
            player_actor_id="player",
            content_locale="zh-CN",
            control=ControlPolicy(mode=ControlMode.STEP, max_steps=4),
        )
    )

    result = service.interactive_turn(
        snapshot.session_id,
        text="林澈，那条消息是不是你发的？",
    )

    runtime = runtime_ref["runtime"]
    npc_actor = next(actor for actor in runtime.actors if actor.name == "lin-che")
    assert isinstance(npc_actor, ConcordiaStoryActor)
    assert isinstance(runtime.resolver, ConcordiaResolverKernel)
    assert gateway.usage.totals().requests == len(transport.calls)
    assert transport.resolution_count == 2
    assert len(transport.observation_prompts) == 1
    assert "faced by 林澈" in transport.observation_prompts[0]
    assert "faced by 你" not in transport.observation_prompts[0]
    assert "faced by 张野" not in transport.observation_prompts[0]
    actor_outputs = [
        call
        for call in transport.calls
        if call.get("model") == "test-provider/actor"
    ]
    assert len(actor_outputs) == 1
    assert result.resolved_turn is not None
    assert result.resolved_turn.events[1].actor_id == "lin-che"
    assert result.resolved_turn.putative_event_text == "林澈，那条消息是不是你发的？"
    assert result.boundary == SimulationBoundary.CHAPTER
