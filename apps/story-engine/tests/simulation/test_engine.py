from datetime import UTC, datetime
from threading import Event, Thread

import pytest

from story_engine.concordia_runtime.factory import (
    ConcordiaActorFactory,
    default_character_recipe,
    default_game_master_recipe,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.replay import ReplayLanguageModel
from story_engine.concordia_runtime.resolver import SimulationCancelledError
from story_engine.domain.action import TaskType
from story_engine.domain.memory import MemoryScope
from story_engine.domain.models import Character
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    StepResult,
    TurnSessionRequest,
    TurnSessionStatus,
)
from story_engine.domain.trace import ModelCallStatus, ModelCallTrace
from story_engine.simulation.engine import (
    InvalidSessionTransitionError,
    StoryTurnEngine,
)
from story_engine.simulation.execution import BranchAlreadyActiveError
from story_engine.simulation.runtime import StorySimulationRuntime


def _runtime_factory(
    *,
    semantic_termination: bool = False,
    boundaries: tuple[str, ...] | None = None,
):
    def build(session_id: str, request: TurnSessionRequest) -> StorySimulationRuntime:
        steps = request.control.max_steps
        boundary_values = boundaries or tuple("none" for _ in range(steps))
        gm_choices = (
            ("Yes",)
            if semantic_termination
            else tuple(
                value
                for step in range(steps)
                for value in ("No", "actor-a")
            )
        )
        gm_text = tuple(
            value
            for step in range(steps)
            for value in (
                f"Observation {step}",
                '{"call_to_action":"Act now.","output_type":"free",'
                '"options":[],"tag":"action"}',
                (
                    '{"event_text":"Resolved event '
                    f'{step}","boundary":"{boundary_values[step]}",'
                    '"visibility":"participants","observer_names":[],'
                    '"participant_names":["Actor A"],"entity_changes":[]}'
                ),
            )
        )
        actor_model = ReplayLanguageModel(
            text_responses=tuple(f"Action {step}" for step in range(steps))
        )
        gm_model = ReplayLanguageModel(
            text_responses=gm_text,
            choice_responses=gm_choices,
        )
        factory = ConcordiaActorFactory({"actor": actor_model, "gm": gm_model})
        actor = factory.build_actor(
            default_character_recipe(
                display_name="Actor A",
                model_profile_id="actor",
                content_locale=request.content_locale,
            ),
            actor_params={
                "name": "actor-a",
                "identity": "Investigator",
                "project_root": ".",
                "branch_id": request.branch_id,
            },
            memory=ConcordiaMemoryBank(
                owner_id="actor-a",
                scope=MemoryScope.CHARACTER,
            ),
        )
        gm = factory.build_game_master(
            default_game_master_recipe(
                model_profile_id="gm",
                content_locale=request.content_locale,
            ),
            gm_params={
                "name": "gm",
                "scene_goal": request.premise_text,
                "project_root": ".",
                "branch_id": request.branch_id,
            },
            actors=(actor,),
            shared_memory=ConcordiaMemoryBank(
                owner_id="gm",
                scope=MemoryScope.GAME_MASTER,
            ),
        )
        return StorySimulationRuntime(
            project_id=request.project_id,
            session_id=session_id,
            branch_id=request.branch_id,
            content_locale=request.content_locale,
            actors=(actor,),
            game_master=gm,
            characters=(
                Character(
                    id="actor-a",
                    display_name="Actor A",
                    type="active",
                    identity="Investigator",
                    core_desire="Discover the truth",
                    current_goal="Enter the archive",
                ),
            ),
        )

    return build


def _request(control: ControlPolicy) -> TurnSessionRequest:
    return TurnSessionRequest(
        project_id="fog-harbor",
        branch_id="main",
        premise_text="Enter the archive.",
        actor_ids=("actor-a",),
        content_locale="en-US",
        control=control,
    )


def _run_to_boundary(
    engine: StoryTurnEngine,
    session_id: str,
    *,
    completed: list | None = None,
) -> object:
    engine.begin_continuous(session_id)
    while True:
        result = engine.advance_one_step(session_id, cancellation=Event())
        if completed is not None:
            completed.append(result)
        if result.status != TurnSessionStatus.RUNNING:
            return engine.get(session_id)


def test_autonomous_run_stops_at_hard_step_limit() -> None:
    engine = StoryTurnEngine(_runtime_factory())
    created = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.AUTONOMOUS, max_steps=3))
    )
    completed_steps = []

    snapshot = _run_to_boundary(
        engine,
        created.session_id,
        completed=completed_steps,
    )

    assert snapshot.status == TurnSessionStatus.TERMINATED
    assert snapshot.current_step == 3
    assert snapshot.termination_reason_text == "maximum step budget reached"
    assert [result.step for result in completed_steps] == [0, 1, 2]
    assert all(result.resolved_turn is not None for result in completed_steps)


def test_replay_runtime_runs_one_hundred_steps_without_state_drift() -> None:
    engine = StoryTurnEngine(_runtime_factory())
    created = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.AUTONOMOUS, max_steps=100))
    )
    completed = []

    snapshot = _run_to_boundary(engine, created.session_id, completed=completed)

    assert snapshot.status == TurnSessionStatus.TERMINATED
    assert snapshot.current_step == 100
    assert snapshot.raw_log_offset == 100
    assert [result.step for result in completed] == list(range(100))
    assert len(engine.pending_memory_records(created.session_id)) == 400


def test_step_mode_pauses_and_resume_runs_exactly_one_more_step() -> None:
    engine = StoryTurnEngine(_runtime_factory())
    created = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.STEP, max_steps=4))
    )

    first = engine.advance_one_step(created.session_id, cancellation=Event())
    after_first = engine.get(created.session_id)
    after_resume = engine.advance_one_step(created.session_id, cancellation=Event())
    after_resume_snapshot = engine.get(created.session_id)

    assert first.status == TurnSessionStatus.PAUSED
    assert after_first.current_step == 1
    assert after_resume.status == TurnSessionStatus.PAUSED
    assert after_resume_snapshot.current_step == 2


def test_scene_mode_pauses_only_at_scene_or_chapter_boundary() -> None:
    engine = StoryTurnEngine(
        _runtime_factory(boundaries=("none", "scene", "none", "none"))
    )
    created = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.SCENE, max_steps=4, max_scenes=4))
    )

    snapshot = _run_to_boundary(engine, created.session_id)

    assert snapshot.status == TurnSessionStatus.PAUSED
    assert snapshot.current_step == 2
    assert snapshot.completed_scenes == 1


def test_chapter_mode_runs_across_scenes_until_chapter_boundary() -> None:
    engine = StoryTurnEngine(
        _runtime_factory(boundaries=("scene", "none", "chapter", "none"))
    )
    created = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.CHAPTER, max_steps=4, max_scenes=4))
    )

    snapshot = _run_to_boundary(engine, created.session_id)

    assert snapshot.status == TurnSessionStatus.PAUSED
    assert snapshot.current_step == 3
    assert snapshot.completed_scenes == 2


def test_autonomous_mode_can_cross_boundaries_until_a_hard_limit() -> None:
    engine = StoryTurnEngine(_runtime_factory(boundaries=("scene", "chapter", "none")))
    created = engine.create_session(
        _request(
            ControlPolicy(
                mode=ControlMode.AUTONOMOUS,
                pause_after_scene=False,
                max_steps=3,
                max_scenes=5,
            )
        )
    )

    snapshot = _run_to_boundary(engine, created.session_id)

    assert snapshot.status == TurnSessionStatus.TERMINATED
    assert snapshot.current_step == 3
    assert snapshot.completed_scenes == 2
    assert snapshot.termination_reason_text == "maximum step budget reached"


def test_max_scenes_is_an_enforced_hard_limit() -> None:
    engine = StoryTurnEngine(_runtime_factory(boundaries=("scene", "scene", "none")))
    created = engine.create_session(
        _request(
            ControlPolicy(
                mode=ControlMode.AUTONOMOUS,
                pause_after_scene=False,
                max_steps=3,
                max_scenes=2,
            )
        )
    )

    snapshot = _run_to_boundary(engine, created.session_id)

    assert snapshot.status == TurnSessionStatus.TERMINATED
    assert snapshot.current_step == 2
    assert snapshot.completed_scenes == 2
    assert snapshot.termination_reason_text == "maximum scene budget reached"


def test_locale_switch_updates_persistent_actor_components_at_boundary() -> None:
    engine = StoryTurnEngine(_runtime_factory())
    created = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.STEP, max_steps=2))
    )

    switched = engine.switch_locale(created.session_id, content_locale="zh-CN")

    actor_state = switched.actor_states["actor-a"]
    components = actor_state["context_components"]
    assert isinstance(components, dict)
    assert components["locale"] == {"content_locale": "zh-CN"}
    assert switched.content_locale == "zh-CN"


def test_game_master_can_end_session_before_actor_action() -> None:
    engine = StoryTurnEngine(_runtime_factory(semantic_termination=True))
    created = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.AUTONOMOUS, max_steps=5))
    )

    snapshot = _run_to_boundary(engine, created.session_id)

    assert snapshot.status == TurnSessionStatus.TERMINATED
    assert snapshot.current_step == 0
    assert snapshot.termination_reason_text == "Game Master ended the session"


def test_precancelled_step_marks_session_cancelled() -> None:
    engine = StoryTurnEngine(_runtime_factory())
    created = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.STEP, max_steps=2))
    )
    cancellation = Event()
    cancellation.set()

    with pytest.raises(SimulationCancelledError):
        engine.advance_one_step(created.session_id, cancellation=cancellation)

    assert engine.get(created.session_id).status == TurnSessionStatus.CANCELLED


def test_cancel_discards_a_result_that_returns_after_engine_cancel() -> None:
    entered = Event()
    release = Event()

    class CancellationIgnoringRuntime:
        def __init__(self, session_id: str, request: TurnSessionRequest) -> None:
            self.session_id = session_id
            self.branch_id = request.branch_id
            self.cancellation = Event()

        def execute_step(self, step: int, *, cancellation: Event) -> StepResult:
            del cancellation
            entered.set()
            assert release.wait(timeout=2)
            return StepResult(
                session_id=self.session_id,
                branch_id=self.branch_id,
                step=step,
                acting_actor_id="actor-a",
                action_spec=None,
                action_text="Continue the investigation.",
                resolved_turn=None,
                status=TurnSessionStatus.RUNNING,
            )

        def actor_states(self):
            return {"actor-a": {}}

        def game_master_states(self):
            return {"gm": {}}

        def drain_stage_events(self):
            return ()

    engine = StoryTurnEngine(CancellationIgnoringRuntime)  # type: ignore[arg-type]
    created = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.STEP, max_steps=2))
    )
    errors: list[Exception] = []

    def advance() -> None:
        try:
            engine.advance_one_step(created.session_id, cancellation=Event())
        except Exception as error:
            errors.append(error)

    thread = Thread(target=advance)
    thread.start()
    assert entered.wait(timeout=1)
    engine.cancel(created.session_id, reason_text="emergency stop")
    release.set()
    thread.join(timeout=2)

    snapshot = engine.get(created.session_id)
    assert not thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], SimulationCancelledError)
    assert snapshot.status == TurnSessionStatus.CANCELLED
    assert snapshot.current_step == 0
    assert snapshot.raw_log_offset == 0


def test_branch_has_only_one_live_session_writer() -> None:
    engine = StoryTurnEngine(_runtime_factory())
    first = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.STEP, max_steps=2))
    )

    with pytest.raises(BranchAlreadyActiveError):
        engine.create_session(
            _request(ControlPolicy(mode=ControlMode.STEP, max_steps=2))
        )

    engine.terminate(first.session_id, reason_text="release branch")
    replacement = engine.create_session(
        _request(ControlPolicy(mode=ControlMode.STEP, max_steps=2))
    )
    assert replacement.status == TurnSessionStatus.CREATED


def test_user_override_policy_blocks_pause_and_safe_terminate_but_not_cancel() -> None:
    engine = StoryTurnEngine(_runtime_factory())
    created = engine.create_session(
        _request(
            ControlPolicy(
                mode=ControlMode.AUTONOMOUS,
                max_steps=2,
                allow_user_override=False,
            )
        )
    )

    with pytest.raises(InvalidSessionTransitionError, match="user overrides"):
        engine.pause(created.session_id)
    with pytest.raises(InvalidSessionTransitionError, match="user overrides"):
        engine.terminate(created.session_id, reason_text="safe stop")

    cancelled = engine.cancel(created.session_id, reason_text="emergency stop")

    assert cancelled.status == TurnSessionStatus.CANCELLED


def test_model_token_budget_terminates_before_another_step_can_run() -> None:
    captured: list[StorySimulationRuntime] = []
    base_factory = _runtime_factory()

    def build(session_id: str, request: TurnSessionRequest) -> StorySimulationRuntime:
        runtime = base_factory(session_id, request)
        captured.append(runtime)
        return runtime

    engine = StoryTurnEngine(build)
    created = engine.create_session(
        _request(
            ControlPolicy(
                mode=ControlMode.AUTONOMOUS,
                max_steps=10,
                max_total_tokens=1,
            )
        )
    )
    now = datetime.now(UTC)
    captured[0]._model_traces.append(
        ModelCallTrace(
            call_id="call:one",
            task_id="task:one",
            task_type=TaskType.ACTOR,
            status=ModelCallStatus.SUCCEEDED,
            session_id=created.session_id,
            branch_id="main",
            profile_id="actor",
                model_ref="replay",
            prompt_version="v1",
            content_locale="en-US",
            message_parts=(),
            prompt_sha256="a" * 64,
            prompt_tokens=1,
            duration_ms=0,
            started_at=now,
            completed_at=now,
        )
    )

    engine.drain_model_traces(created.session_id)
    snapshot = engine.get(created.session_id)

    assert snapshot.status == TurnSessionStatus.TERMINATED
    assert snapshot.total_model_tokens == 1
    assert snapshot.termination_reason_text == "maximum token budget reached"
