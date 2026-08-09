# ruff: noqa: RUF001

from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace

from story_engine.concordia_runtime.prefabs.game_master import _resolve_story_event
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
from story_engine.simulation.command_service import SimulationCommandService
from story_engine.simulation.commands import SessionCommandCoordinator
from story_engine.simulation.engine import StoryTurnEngine
from story_engine.simulation.persistence import SimulationPersistenceService
from story_engine.simulation.projection_coordinator import (
    SimulationProjectionCoordinator,
)
from story_engine.simulation.runtime import StorySimulationRuntime


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

    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.selected_candidates: list[tuple[str, ...]] = []
        self._state: dict[str, object] = {}

    def set_active_actor(self, _display_name: str) -> None:
        return None

    def should_terminate(self, **_kwargs: object) -> tuple[bool, None]:
        return False, None

    def make_observation(
        self,
        actor: RecordingActor,
        **kwargs: object,
    ) -> PerceptionFrame:
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
    game_master = RecordingGameMaster(log)
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
                max_scenes=5,
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
    assert actors["lin-che"].act_calls == 1
    assert actors["bystander"].act_calls == 0
