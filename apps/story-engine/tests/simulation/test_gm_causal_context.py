from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from story_engine.concordia_runtime.resolver import ConcordiaResolverKernel
from story_engine.domain.models import Character, WorldState
from story_engine.domain.projection import (
    EventVisibility,
    ResolvedEvent,
    ResolvedTurn,
    SimulationBoundary,
)
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    InitiativeTrigger,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import ModelCallStatus, TurnTrace
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.simulation.runtime import StorySimulationRuntime
from story_engine.simulation.session import calculate_snapshot_state_hash


def _snapshot(
    *,
    step: int,
    branch_id: str = "main",
    history_head_id: str | None = None,
) -> TurnSessionSnapshot:
    now = datetime.now(UTC)
    request = TurnSessionRequest(
        project_id="project-1",
        branch_id=branch_id,
        premise_text="Test causal continuity.",
        actor_ids=("actor-a",),
        content_locale="zh-CN",
        control=ControlPolicy(mode=ControlMode.STEP),
    )
    provisional = TurnSessionSnapshot(
        session_id="session:1",
        project_id="project-1",
        branch_id=branch_id,
        status=TurnSessionStatus.PAUSED,
        content_locale="zh-CN",
        request=request,
        current_step=step,
        actor_states={"actor-a": {}},
        game_master_states={"gm": {}},
        raw_log_offset=step,
        history_head_id=history_head_id,
        started_at=now,
        updated_at=now,
        state_hash="0" * 64,
    )
    return provisional.model_copy(
        update={"state_hash": calculate_snapshot_state_hash(provisional)}
    )


def _event(
    text: str,
    *,
    step: int,
    branch_id: str = "main",
    boundary: SimulationBoundary = SimulationBoundary.NONE,
) -> tuple[ResolvedEvent, ResolvedTurn]:
    event = ResolvedEvent(
        event_id=f"event:{branch_id}:{step}",
        session_id="session:1",
        step=step,
        actor_id="actor-a",
        event_text=text,
        visibility=EventVisibility.PUBLIC,
        participant_ids=("actor-a",),
        content_locale="zh-CN",
        occurred_at=datetime(2026, 8, 16, 12, step, tzinfo=UTC),
    )
    return event, ResolvedTurn(
        session_id="session:1",
        branch_id=branch_id,
        step=step,
        acting_actor_id="actor-a",
        putative_event_text="test intent",
        raw_resolution_text=text,
        events=(event,),
        boundary=boundary,
        content_locale="zh-CN",
    )


def _commit_event(
    kernel: SimulationCommitKernel,
    *,
    head,
    text: str,
    step: int,
    branch_id: str = "main",
    boundary: SimulationBoundary = SimulationBoundary.NONE,
):
    _resolved_event, resolved_turn = _event(
        text,
        step=step,
        branch_id=branch_id,
        boundary=boundary,
    )
    result = StepResult(
        session_id="session:1",
        branch_id=branch_id,
        step=step,
        acting_actor_id="actor-a",
        action_spec=None,
        action_text=f"intent {step}",
        resolved_turn=resolved_turn,
        status=TurnSessionStatus.PAUSED,
    )
    now = datetime.now(UTC)
    trace = TurnTrace(
        trace_id=f"trace:{branch_id}:{step}",
        session_id="session:1",
        branch_id=branch_id,
        step=step,
        content_locale="zh-CN",
        stages=(),
        model_calls=(),
        acting_actor_id="actor-a",
        started_at=now,
        completed_at=now,
        status=ModelCallStatus.SUCCEEDED,
    )
    committed = kernel.append_step(
        result,
        _snapshot(
            step=step + 1,
            branch_id=branch_id,
            history_head_id=head.history_head_id,
        ),
        trace,
    )
    assert committed is not None
    return committed, resolved_turn


def _runtime(
    project_root: Path,
    *,
    branch_id: str = "main",
    pending_scene_events: tuple[ResolvedEvent, ...] = (),
) -> StorySimulationRuntime:
    actor_state: dict[str, object] = {}
    actor = SimpleNamespace(
        name="actor-a",
        display_name="调查员",
        get_state=lambda: dict(actor_state),
        set_state=lambda value: (actor_state.clear(), actor_state.update(value)),
        set_actor_state=lambda value: actor_state.__setitem__("actor_state", value),
    )
    character = Character(
        id="actor-a",
        display_name="调查员",
        type="active",
        identity="追查失窃相机的人",
        core_desire="找回相机",
        current_goal="辨认黑衣人",
        location="候车厅",
    )
    return StorySimulationRuntime(
        project_id="project-1",
        session_id="session:1",
        branch_id=branch_id,
        content_locale="zh-CN",
        actors=(actor,),
        game_master=SimpleNamespace(name="gm"),
        characters=(character,),
        world=WorldState(current_time="21:00", current_location="候车厅"),
        project_root=project_root,
        pending_scene_events=pending_scene_events,
        initial_roster_selected=True,
    )


def test_previous_event_survives_scene_boundary(tmp_path: Path) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    genesis = kernel.save_checkpoint(_snapshot(step=0), reason="created")
    committed, turn_a = _commit_event(
        kernel,
        head=genesis,
        text="相机被黑衣人拿走。",
        step=0,
        boundary=SimulationBoundary.SCENE,
    )
    assert committed.history_head_id
    runtime = _runtime(tmp_path)

    runtime._advance_scene_boundary(
        turn_a,
        event_id=turn_a.events[0].event_id,
        started_at=datetime.now(UTC),
    )
    context = runtime._resolver_context(
        step=1,
        acting_actor_id="actor-a",
        putative_event_text="我追出候车厅。",
    )

    assert runtime.pending_scene_events() == ()
    assert context.recent_committed_events == ("相机被黑衣人拿走。",)


def test_resolution_context_has_exact_immediate_previous_committed_event(
    tmp_path: Path,
) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    genesis = kernel.save_checkpoint(_snapshot(step=0), reason="created")
    _commit_event(
        kernel,
        head=genesis,
        text="相机被黑衣人拿走。",
        step=0,
    )
    decoy, _turn_decoy = _event("未提交的场景草稿。", step=1)
    runtime = _runtime(tmp_path, pending_scene_events=(decoy,))

    context = runtime._resolver_context(
        step=1,
        acting_actor_id="actor-a",
        putative_event_text="你看清他的脸了吗?",
    )
    prompt = ConcordiaResolverKernel._resolution_context_prompt(context)

    assert context.immediate_previous_committed_event == "相机被黑衣人拿走。"
    assert context.recent_committed_events == ("相机被黑衣人拿走。",)
    assert "Immediate Previous Committed Event:\n相机被黑衣人拿走。" in prompt
    assert "未提交的场景草稿" not in prompt


def test_recent_committed_events_follow_only_current_branch_lineage(
    tmp_path: Path,
) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    genesis = kernel.save_checkpoint(_snapshot(step=0), reason="created")
    fork_point, _turn_a = _commit_event(
        kernel,
        head=genesis,
        text="A: 相机被拿走。",
        step=0,
    )
    kernel.create_branch(
        "project-1",
        source_checkpoint_id=fork_point.checkpoint_id,
        branch_id="fork",
        parent_branch_id="main",
        content_locale="zh-CN",
    )
    _commit_event(
        kernel,
        head=fork_point,
        text="B: 主线追向站台。",
        step=1,
    )
    _commit_event(
        kernel,
        head=fork_point,
        text="C: 分支留在候车厅。",
        step=1,
        branch_id="fork",
    )

    recent = _runtime(tmp_path, branch_id="fork").recent_committed_events()

    assert tuple(event.event_text for event in recent) == (
        "A: 相机被拿走。",
        "C: 分支留在候车厅。",
    )
    assert all(event.event_text != "B: 主线追向站台。" for event in recent)


def test_initiative_uses_committed_history_after_scene_boundary(
    tmp_path: Path,
) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    genesis = kernel.save_checkpoint(_snapshot(step=0), reason="created")
    _committed, turn_a = _commit_event(
        kernel,
        head=genesis,
        text="暴雨淹没了通往码头的路。",
        step=0,
        boundary=SimulationBoundary.SCENE,
    )
    runtime = _runtime(tmp_path)
    runtime._advance_scene_boundary(
        turn_a,
        event_id=turn_a.events[0].event_id,
        started_at=datetime.now(UTC),
    )

    context = runtime._initiative_context(
        step=1,
        trigger=InitiativeTrigger(
            reason="stagnation",
            description="世界需要产生新的外部变化。",
        ),
    )

    assert runtime.pending_scene_events() == ()
    assert context.recent_committed_events == ("暴雨淹没了通往码头的路。",)
