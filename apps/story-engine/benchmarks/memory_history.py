"""Benchmark durable memory history at 100, 300, and 1,000 turns."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import ModelCallStatus, TurnTrace
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.simulation.session import calculate_snapshot_state_hash

_STARTED_AT = datetime(2026, 8, 11, tzinfo=UTC)


def _snapshot(
    step: int,
    *,
    branch_id: str = "main",
    history_head_id: str | None = None,
) -> TurnSessionSnapshot:
    request = TurnSessionRequest(
        project_id="memory-benchmark",
        branch_id=branch_id,
        premise_text="Measure durable cognitive history.",
        actor_ids=("actor-a",),
        content_locale="en-US",
        control=ControlPolicy(mode=ControlMode.STEP),
    )
    provisional = TurnSessionSnapshot(
        session_id="session:memory-benchmark",
        project_id=request.project_id,
        branch_id=branch_id,
        status=TurnSessionStatus.PAUSED,
        content_locale=request.content_locale,
        request=request,
        current_step=step,
        actor_states={"actor-a": {"step": step}},
        game_master_states={"gm": {"step": step}},
        raw_log_offset=step,
        history_head_id=history_head_id,
        started_at=_STARTED_AT,
        updated_at=_STARTED_AT + timedelta(seconds=step),
        state_hash="0" * 64,
    )
    return provisional.model_copy(
        update={"state_hash": calculate_snapshot_state_hash(provisional)}
    )


def _memory(step: int, *, branch_id: str = "main") -> MemoryRecord:
    return MemoryRecord(
        record_id=f"memory:{branch_id}:{step}",
        record_type=MemoryRecordType.OBSERVATION,
        scope=MemoryScope.CHARACTER,
        owner_id="actor-a",
        session_id="session:memory-benchmark",
        branch_id=branch_id,
        step=step,
        text=f"Bounded benchmark observation {step} on {branch_id}.",
        content_locale="en-US",
        created_at=_STARTED_AT + timedelta(seconds=step),
        actor_ids=("actor-a",),
        visible_to=("actor-a",),
        tags=("benchmark", "observation"),
    )


def _result(step: int, *, branch_id: str = "main") -> StepResult:
    return StepResult(
        session_id="session:memory-benchmark",
        branch_id=branch_id,
        step=step,
        acting_actor_id="actor-a",
        action_spec=None,
        action_text=f"Benchmark action {step}.",
        resolved_turn=None,
        status=TurnSessionStatus.PAUSED,
    )


def _trace(step: int, *, branch_id: str = "main") -> TurnTrace:
    occurred_at = _STARTED_AT + timedelta(seconds=step)
    return TurnTrace(
        trace_id=f"trace:{branch_id}:{step}",
        session_id="session:memory-benchmark",
        branch_id=branch_id,
        step=step,
        content_locale="en-US",
        stages=(),
        model_calls=(),
        acting_actor_id="actor-a",
        started_at=occurred_at,
        completed_at=occurred_at,
        status=ModelCallStatus.SUCCEEDED,
    )


def _percentile(samples: list[float], percentile: float) -> float:
    ordered = sorted(samples)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return ordered[index]


def _disk_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def benchmark(turn_count: int) -> dict[str, float | int]:
    with tempfile.TemporaryDirectory(prefix=f"memory-history-{turn_count}-") as raw:
        root = Path(raw)
        kernel = SimulationCommitKernel(root)
        genesis = kernel.save_checkpoint(
            _snapshot(0),
            reason="Genesis",
            genesis_memory_delta=(_memory(0),),
        )
        head = genesis
        fork_point = genesis
        commit_ms: list[float] = []
        for step in range(turn_count):
            started = time.perf_counter()
            head = kernel.append_step(
                _result(step),
                _snapshot(step + 1, history_head_id=head.history_head_id),
                _trace(step),
                memory_delta=(_memory(step + 1),),
            )
            commit_ms.append((time.perf_counter() - started) * 1_000)
            if step + 1 == turn_count // 2:
                fork_point = head

        restore_started = time.perf_counter()
        restored = kernel.logs.reachable_memory_records(
            kernel.checkpoints,
            head.checkpoint_id,
            branch_id="main",
        )
        restore_ms = (time.perf_counter() - restore_started) * 1_000

        kernel.create_branch(
            "memory-benchmark",
            source_checkpoint_id=fork_point.checkpoint_id,
            branch_id="alternate",
            parent_branch_id="main",
            content_locale="en-US",
        )
        alternate = kernel.append_step(
            _result(turn_count // 2, branch_id="alternate"),
            _snapshot(
                turn_count // 2 + 1,
                branch_id="alternate",
                history_head_id=fork_point.history_head_id,
            ),
            _trace(turn_count // 2, branch_id="alternate"),
            memory_delta=(_memory(turn_count // 2 + 1, branch_id="alternate"),),
        )
        branch_started = time.perf_counter()
        branch_records = kernel.logs.reachable_memory_records(
            kernel.checkpoints,
            alternate.checkpoint_id,
            branch_id="alternate",
        )
        branch_restore_ms = (time.perf_counter() - branch_started) * 1_000

        return {
            "turns": turn_count,
            "disk_bytes": _disk_bytes(root),
            "commit_p50_ms": statistics.median(commit_ms),
            "commit_p95_ms": _percentile(commit_ms, 0.95),
            "restore_ms": restore_ms,
            "restored_records": len(restored),
            "branch_restore_ms": branch_restore_ms,
            "branch_records": len(branch_records),
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("turns", nargs="*", type=int, default=[100, 300, 1000])
    args = parser.parse_args()
    if any(turns < 2 for turns in args.turns):
        parser.error("turn counts must be at least 2")
    results = [benchmark(turns) for turns in args.turns]
    baseline = results[0]
    for result in results:
        result["disk_vs_first"] = result["disk_bytes"] / baseline["disk_bytes"]
        result["restore_vs_first"] = result["restore_ms"] / baseline["restore_ms"]
    print(json.dumps(results, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
