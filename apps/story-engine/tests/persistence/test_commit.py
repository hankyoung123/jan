from datetime import UTC, datetime
from pathlib import Path

import pytest

from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.projection import (
    EventVisibility,
    ResolvedEvent,
    ResolvedTurn,
    SimulationBoundary,
)
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import ModelCallStatus, TurnTrace
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.simulation.session import calculate_snapshot_state_hash
from story_engine.workspace.documents import load_json_envelope


def _snapshot(
    *,
    step: int,
    branch_id: str = "main",
    status: TurnSessionStatus = TurnSessionStatus.PAUSED,
    marker: str | None = None,
) -> TurnSessionSnapshot:
    now = datetime.now(UTC)
    provisional = TurnSessionSnapshot(
        session_id="session:1",
        project_id="fog-harbor",
        branch_id=branch_id,
        status=status,
        content_locale="en-US",
        request=TurnSessionRequest(
            project_id="fog-harbor",
            branch_id=branch_id,
            premise_text="Test checkpoint",
            actor_ids=("actor-a",),
            content_locale="en-US",
            control=ControlPolicy(mode=ControlMode.STEP),
        ),
        current_step=step,
        actor_states={"actor-a": {"step": step, "marker": marker}},
        game_master_states={"gm": {"step": step}},
        memory_snapshots={},
        raw_log_offset=step,
        started_at=now,
        updated_at=now,
        state_hash="0" * 64,
    )
    return provisional.model_copy(
        update={"state_hash": calculate_snapshot_state_hash(provisional)}
    )


def _result(step: int) -> StepResult:
    return StepResult(
        session_id="session:1",
        branch_id="main",
        step=step,
        acting_actor_id="actor-a",
        action_spec=None,
        action_text=f"action {step}",
        resolved_turn=None,
        status=TurnSessionStatus.PAUSED,
    )


def _resolved_result(step: int, event_text: str) -> StepResult:
    now = datetime.now(UTC)
    event = ResolvedEvent(
        event_id=f"event:{event_text.replace(' ', '-')}",
        session_id="session:1",
        step=step,
        actor_id="actor-a",
        event_text=event_text,
        visibility=EventVisibility.PUBLIC,
        participant_ids=("actor-a",),
        content_locale="en-US",
        occurred_at=now,
    )
    turn = ResolvedTurn(
        session_id="session:1",
        branch_id="main",
        step=step,
        acting_actor_id="actor-a",
        raw_resolution_text=event_text,
        events=(event,),
        boundary=SimulationBoundary.NONE,
        content_locale="en-US",
    )
    return _result(step).model_copy(update={"resolved_turn": turn})


def _snapshot_with_observation(
    *,
    current_step: int,
    observation_step: int,
    text: str,
    status: TurnSessionStatus,
) -> TurnSessionSnapshot:
    snapshot = _snapshot(step=current_step, status=status)
    memory = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
    )
    memory.add(
        MemoryRecord(
            record_id=f"observation:session:1:{observation_step}:actor-a",
            record_type=MemoryRecordType.OBSERVATION,
            scope=MemoryScope.CHARACTER,
            owner_id="actor-a",
            session_id="session:1",
            branch_id="main",
            step=observation_step,
            text=text,
            content_locale="en-US",
            created_at=datetime.now(UTC),
            actor_ids=("actor-a",),
            tags=("observation",),
            visible_to=("actor-a",),
        )
    )
    provisional = snapshot.model_copy(
        update={"memory_snapshots": {"actor-a": memory.snapshot()}}
    )
    return provisional.model_copy(
        update={"state_hash": calculate_snapshot_state_hash(provisional)}
    )


def _trace(
    step: int,
    *,
    status: ModelCallStatus = ModelCallStatus.SUCCEEDED,
    attempt: int = 0,
) -> TurnTrace:
    now = datetime.now(UTC)
    return TurnTrace(
        trace_id=f"trace:session-1:{step}:{attempt}",
        session_id="session:1",
        branch_id="main",
        step=step,
        content_locale="en-US",
        stages=(),
        model_calls=(),
        acting_actor_id="actor-a",
        started_at=now,
        completed_at=now,
        status=status,
    )


def test_checkpoint_round_trip_verifies_state_hash(tmp_path: Path) -> None:
    store = CheckpointStore(tmp_path)
    original = _snapshot(step=2)
    checkpoint_id, path = store.save(original)

    loaded = store.load(checkpoint_id)

    assert loaded.current_step == 2
    assert loaded.checkpoint_id == checkpoint_id
    assert loaded.state_hash == original.state_hash
    assert store.parent_id(checkpoint_id) is None

    payload = load_json_envelope(path, schema="story-engine/checkpoint/v1")
    assert payload["snapshot"]["current_step"] == 2
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"current_step": 2',
            '"current_step": 999',
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="state hash"):
        store.load(checkpoint_id)


def test_commit_writes_checkpoint_log_then_advances_branch(tmp_path: Path) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    initial = kernel.save_checkpoint(_snapshot(step=0), reason="session created")
    committed = kernel.append_step(_result(0), _snapshot(step=1), _trace(0))

    branch = kernel.branches.load("main")
    records = kernel.logs.read("main")

    assert initial.branch.head_step == 0
    assert branch.head_checkpoint_id == committed.checkpoint_id
    assert branch.head_step == 1
    assert records[0].checkpoint_id == committed.checkpoint_id
    loaded = kernel.load_checkpoint("fog-harbor", committed.checkpoint_id)
    assert loaded.current_step == 1


def test_failed_nth_file_write_rolls_back_the_complete_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    initial = kernel.save_checkpoint(_snapshot(step=0), reason="session created")

    from story_engine.workspace import transaction

    real_replace = transaction._replace
    replacements = 0

    def fail_third_write(source: Path, destination: Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 3:
            raise OSError("simulated transaction failure")
        real_replace(source, destination)

    monkeypatch.setattr(transaction, "_replace", fail_third_write)
    next_snapshot = _snapshot(step=1)
    next_checkpoint_id = kernel.checkpoints.prepare(
        next_snapshot,
        parent_checkpoint_id=initial.checkpoint_id,
    )[0]
    with pytest.raises(OSError, match="transaction failure"):
        kernel.append_step(_result(0), next_snapshot, _trace(0))

    branch = kernel.branches.load("main")
    assert branch.head_checkpoint_id == initial.checkpoint_id
    assert kernel.checkpoints.exists(initial.checkpoint_id)
    assert not kernel.checkpoints.exists(next_checkpoint_id)
    assert kernel.logs.read("main") == ()
    manifest = kernel.sessions.load("session:1")
    assert manifest.current_step == 0
    assert manifest.head_checkpoint_id == initial.checkpoint_id
    assert not any((tmp_path / ".story-engine/recovery").iterdir())


def test_step_authority_uses_only_markdown_files(tmp_path: Path) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    kernel.save_checkpoint(_snapshot(step=0), reason="created")
    kernel.append_step(_result(0), _snapshot(step=1), _trace(0))

    prohibited = tuple(
        path
        for path in tmp_path.rglob("*")
        if path.is_file()
        and path.suffix in {".json", ".jsonl"}
        and ".story-engine/recovery" not in path.as_posix()
    )
    assert prohibited == ()
    assert tuple((tmp_path / "history/turns/main").glob("*.md"))


def test_failed_step_trace_can_be_followed_by_successful_checkpoint_retry(
    tmp_path: Path,
) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    kernel.save_checkpoint(_snapshot(step=0), reason="created")
    failed_result = _result(0).model_copy(update={"status": TurnSessionStatus.FAILED})
    kernel.append_step(
        failed_result,
        _snapshot(step=0, status=TurnSessionStatus.FAILED),
        _trace(0, status=ModelCallStatus.FAILED),
        checkpoint=False,
    )

    committed = kernel.append_step(
        _result(0),
        _snapshot(step=1),
        _trace(0, attempt=1),
    )

    records = kernel.logs.read("main")
    assert committed is not None
    assert [record.trace.status for record in records] == [
        ModelCallStatus.FAILED,
        ModelCallStatus.SUCCEEDED,
    ]
    assert kernel.branches.load("main").head_step == 1


def test_failed_step_trace_does_not_persist_uncommitted_observations(
    tmp_path: Path,
) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    kernel.save_checkpoint(_snapshot(step=0), reason="created")
    failed_result = _result(0).model_copy(update={"status": TurnSessionStatus.FAILED})
    failed_snapshot = _snapshot_with_observation(
        current_step=0,
        observation_step=0,
        text="uncommitted observation",
        status=TurnSessionStatus.FAILED,
    )

    kernel.append_step(
        failed_result,
        failed_snapshot,
        _trace(0, status=ModelCallStatus.FAILED),
        checkpoint=False,
    )

    assert kernel.logs.read_observations("main") == ()


def test_successful_retry_replaces_legacy_observation_from_failed_attempt(
    tmp_path: Path,
) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    kernel.save_checkpoint(_snapshot(step=0), reason="created")
    failed_result = _result(0).model_copy(update={"status": TurnSessionStatus.FAILED})
    failed_snapshot = _snapshot_with_observation(
        current_step=0,
        observation_step=0,
        text="legacy failed observation",
        status=TurnSessionStatus.FAILED,
    )
    kernel.append_step(
        failed_result,
        failed_snapshot,
        _trace(0, status=ModelCallStatus.FAILED),
        checkpoint=False,
    )
    ((legacy_path, legacy_content),) = kernel.logs.prepare_observations(
        failed_snapshot,
        step=0,
    )
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(legacy_content, encoding="utf-8")

    retry_snapshot = _snapshot_with_observation(
        current_step=1,
        observation_step=0,
        text="committed retry observation",
        status=TurnSessionStatus.PAUSED,
    )
    committed = kernel.append_step(
        _result(0),
        retry_snapshot,
        _trace(0, attempt=1),
    )

    assert committed is not None
    assert [item.text for item in kernel.logs.read_observations("main")] == [
        "committed retry observation"
    ]
    assert committed.checkpoint_id in kernel.checkpoints.lineage(
        committed.checkpoint_id
    )


def test_branch_fork_and_rollback_keep_independent_heads(tmp_path: Path) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    first = kernel.save_checkpoint(_snapshot(step=0), reason="created")
    second = kernel.append_step(_result(0), _snapshot(step=1), _trace(0))
    fork = kernel.create_branch(
        "fog-harbor",
        source_checkpoint_id=first.checkpoint_id,
        branch_id="branch-b",
        parent_branch_id="main",
        content_locale="en-US",
    )

    rolled_back = kernel.rollback_branch(
        "fog-harbor",
        "main",
        checkpoint_id=first.checkpoint_id,
    )

    assert fork.head_checkpoint_id == first.checkpoint_id
    assert fork.parent_branch_id == "main"
    assert rolled_back.head_checkpoint_id == first.checkpoint_id
    assert kernel.branches.load("branch-b").head_checkpoint_id == first.checkpoint_id
    assert second.checkpoint_id != first.checkpoint_id


def test_reachable_history_excludes_abandoned_turn_after_rollback(
    tmp_path: Path,
) -> None:
    kernel = SimulationCommitKernel(tmp_path)
    initial = kernel.save_checkpoint(_snapshot(step=0), reason="created")
    abandoned = kernel.append_step(
        _resolved_result(0, "abandoned event"),
        _snapshot(step=1, marker="abandoned"),
        _trace(0),
    )
    kernel.rollback_branch(
        "fog-harbor",
        "main",
        checkpoint_id=initial.checkpoint_id,
    )
    replacement = kernel.append_step(
        _resolved_result(0, "replacement event"),
        _snapshot(step=1, marker="replacement"),
        _trace(0, attempt=1),
    )

    assert abandoned is not None
    assert replacement is not None
    records = kernel.logs.reachable(kernel.checkpoints, replacement.checkpoint_id)
    assert [record.result.resolved_turn.events[0].event_text for record in records] == [
        "replacement event"
    ]
    assert abandoned.checkpoint_id not in kernel.checkpoints.lineage(
        replacement.checkpoint_id
    )
