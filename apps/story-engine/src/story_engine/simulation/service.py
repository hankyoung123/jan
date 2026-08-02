import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event

from pydantic import JsonValue

from story_engine.domain.simulation import (
    CommitResult,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import ModelCallStatus, TurnTrace
from story_engine.events.stream import EngineEventBus, EngineEventType
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.simulation.engine import StoryTurnEngine

CommitKernelFactory = Callable[[str], SimulationCommitKernel]


class SimulationApplicationService:
    def __init__(
        self,
        engine: StoryTurnEngine,
        event_bus: EngineEventBus,
        commit_kernel_factory: CommitKernelFactory | None = None,
    ) -> None:
        self.engine = engine
        self.event_bus = event_bus
        self._commit_kernel_factory = commit_kernel_factory

    def _publish(
        self,
        snapshot: TurnSessionSnapshot,
        event_type: EngineEventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        self.event_bus.publish(
            project_id=snapshot.project_id,
            subject_id=snapshot.session_id,
            event_type=event_type,
            payload=payload or {"session_id": snapshot.session_id},
        )

    def start(self, request: TurnSessionRequest) -> TurnSessionSnapshot:
        snapshot = self.engine.create_session(request)
        if self._commit_kernel_factory is not None:
            try:
                committed = self._commit_kernel_factory(
                    snapshot.project_id
                ).save_checkpoint(snapshot, reason="session created")
                snapshot = self.engine.attach_checkpoint(
                    snapshot.session_id,
                    committed.checkpoint_id,
                )
                self._publish(snapshot, "simulation.checkpointed")
            except Exception as error:
                self.engine.fail(snapshot.session_id, reason_text=str(error))
                raise
        self._publish(snapshot, "simulation.started")
        return snapshot

    def get(self, session_id: str) -> TurnSessionSnapshot:
        return self.engine.get(session_id)

    def _step_sink(
        self,
        snapshot_getter: Callable[[], TurnSessionSnapshot],
    ) -> Callable[[StepResult], None]:
        def publish(result: StepResult) -> None:
            snapshot = snapshot_getter()
            self._publish(
                snapshot,
                "simulation.step.completed",
                payload={
                    "session_id": result.session_id,
                    "step": result.step,
                    "acting_actor_id": result.acting_actor_id,
                    "status": result.status.value,
                },
            )

        return publish

    def _trace_for(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> TurnTrace:
        now = datetime.now(UTC)
        return TurnTrace(
            trace_id=f"trace:{uuid.uuid4().hex}",
            session_id=result.session_id,
            branch_id=result.branch_id,
            step=result.step,
            content_locale=snapshot.content_locale,
            stages=(),
            model_calls=self.engine.drain_model_traces(result.session_id),
            action_spec=result.action_spec,
            acting_actor_id=result.acting_actor_id,
            putative_event_record_id=(
                f"putative:{result.session_id}:{result.step}"
                if result.resolved_turn is not None
                else None
            ),
            resolved_event_record_ids=(
                (f"event:{result.session_id}:{result.step}",)
                if result.resolved_turn is not None
                else ()
            ),
            started_at=now,
            completed_at=now,
            status=ModelCallStatus.SUCCEEDED,
        )

    def _commit_step(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> tuple[StepResult, TurnSessionSnapshot, CommitResult | None]:
        trace = self._trace_for(result, snapshot)
        snapshot = self.engine.get(result.session_id)
        result = result.model_copy(update={"status": snapshot.status})
        if self._commit_kernel_factory is None:
            return result, snapshot, None
        try:
            committed = self._commit_kernel_factory(snapshot.project_id).append_step(
                result,
                snapshot,
                trace,
            )
            snapshot = self.engine.attach_checkpoint(
                snapshot.session_id,
                committed.checkpoint_id,
            )
            result = result.model_copy(
                update={"checkpoint_id": committed.checkpoint_id}
            )
            self._publish(
                snapshot,
                "simulation.checkpointed",
                payload={
                    "session_id": snapshot.session_id,
                    "checkpoint_id": committed.checkpoint_id,
                    "step": snapshot.current_step,
                },
            )
            return result, snapshot, committed
        except Exception as error:
            failed = self.engine.fail(snapshot.session_id, reason_text=str(error))
            self._publish(
                failed,
                "simulation.failed",
                payload={"session_id": failed.session_id, "reason": str(error)},
            )
            raise

    def step(self, session_id: str, *, cancellation: Event) -> StepResult:
        result = self.engine.step(session_id, cancellation=cancellation)
        snapshot = self.engine.get(session_id)
        result, snapshot, _ = self._commit_step(result, snapshot)
        self._step_sink(lambda: snapshot)(result)
        if snapshot.status == TurnSessionStatus.PAUSED:
            self._publish(snapshot, "simulation.paused")
        elif snapshot.status == TurnSessionStatus.TERMINATED:
            self._publish(snapshot, "simulation.terminated")
        return result

    def run(self, session_id: str, *, cancellation: Event) -> TurnSessionSnapshot:
        def commit_and_publish(result: StepResult) -> None:
            snapshot = self.engine.get(session_id)
            committed_result, committed_snapshot, _ = self._commit_step(
                result,
                snapshot,
            )
            self._step_sink(lambda: committed_snapshot)(committed_result)

        snapshot = self.engine.run(
            session_id,
            cancellation=cancellation,
            on_step=commit_and_publish,
        )
        event_type: EngineEventType = (
            "simulation.paused"
            if snapshot.status == TurnSessionStatus.PAUSED
            else "simulation.terminated"
        )
        self._publish(snapshot, event_type)
        return snapshot

    def pause(self, session_id: str) -> TurnSessionSnapshot:
        snapshot = self.engine.pause(session_id)
        self._publish(snapshot, "simulation.paused")
        return snapshot

    def resume(self, session_id: str, *, cancellation: Event) -> TurnSessionSnapshot:
        def commit_and_publish(result: StepResult) -> None:
            snapshot = self.engine.get(session_id)
            committed_result, committed_snapshot, _ = self._commit_step(
                result,
                snapshot,
            )
            self._step_sink(lambda: committed_snapshot)(committed_result)

        snapshot = self.engine.resume(
            session_id,
            cancellation=cancellation,
            on_step=commit_and_publish,
        )
        event_type: EngineEventType = (
            "simulation.paused"
            if snapshot.status == TurnSessionStatus.PAUSED
            else "simulation.terminated"
        )
        self._publish(snapshot, event_type)
        return snapshot

    def terminate(self, session_id: str, *, reason_text: str) -> TurnSessionSnapshot:
        snapshot = self.engine.terminate(session_id, reason_text=reason_text)
        self._publish(snapshot, "simulation.terminated")
        return snapshot

    def checkpoint(self, session_id: str, *, reason: str) -> CommitResult:
        if self._commit_kernel_factory is None:
            raise RuntimeError("simulation persistence is not configured")
        snapshot = self.engine.get(session_id)
        committed = self._commit_kernel_factory(snapshot.project_id).save_checkpoint(
            snapshot,
            reason=reason,
        )
        snapshot = self.engine.attach_checkpoint(
            session_id,
            committed.checkpoint_id,
        )
        self._publish(
            snapshot,
            "simulation.checkpointed",
            payload={
                "session_id": session_id,
                "checkpoint_id": committed.checkpoint_id,
                "step": snapshot.current_step,
            },
        )
        return committed

    def switch_locale(
        self,
        session_id: str,
        *,
        content_locale: str,
    ) -> TurnSessionSnapshot:
        snapshot = self.engine.switch_locale(
            session_id,
            content_locale=content_locale,
        )
        if self._commit_kernel_factory is not None:
            committed = self._commit_kernel_factory(
                snapshot.project_id
            ).save_checkpoint(snapshot, reason="content locale changed")
            snapshot = self.engine.attach_checkpoint(
                session_id,
                committed.checkpoint_id,
            )
            self._publish(snapshot, "simulation.checkpointed")
        return snapshot

    def checkpoint_inactive_sessions(self) -> tuple[CommitResult, ...]:
        if self._commit_kernel_factory is None:
            return ()
        committed = []
        for snapshot in self.engine.list_snapshots():
            if snapshot.status not in {
                TurnSessionStatus.CREATED,
                TurnSessionStatus.PAUSED,
            }:
                continue
            committed.append(
                self.checkpoint(snapshot.session_id, reason="application shutdown")
            )
        return tuple(committed)
