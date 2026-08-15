"""Durable simulation state and step commit coordination."""

import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime

from pydantic import JsonValue

from story_engine.domain.projection import ResolvedEvent, SimulationBoundary
from story_engine.domain.session_manifest import SessionManifest
from story_engine.domain.simulation import (
    CommitResult,
    PendingControl,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import (
    ModelCallStatus,
    SimulationObserver,
    SimulationStage,
    SimulationStageEvent,
    StageStatus,
    StageTrace,
    TurnTrace,
)
from story_engine.events.stream import EngineEventBus, EngineEventType
from story_engine.persistence.command_store import CommandReceiptCommit
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.simulation.engine import (
    SessionNotFoundError,
    StoryTurnEngine,
)
from story_engine.simulation.session import calculate_snapshot_state_hash

CommitKernelFactory = Callable[[str], SimulationCommitKernel]


class SimulationPersistenceService:
    """Own manifests, checkpoints, traces, and atomic step commits."""

    def __init__(
        self,
        engine: StoryTurnEngine,
        event_bus: EngineEventBus,
        commit_kernel_factory: CommitKernelFactory | None = None,
    ) -> None:
        self.engine = engine
        self.event_bus = event_bus
        self._commit_kernel_factory = commit_kernel_factory
        self._last_started_step: dict[str, int] = {}

    @property
    def configured(self) -> bool:
        return self._commit_kernel_factory is not None

    def kernel(self, project_id: str) -> SimulationCommitKernel:
        if self._commit_kernel_factory is None:
            raise RuntimeError("simulation persistence is not configured")
        return self._commit_kernel_factory(project_id)

    def current_scene_events(
        self,
        snapshot: TurnSessionSnapshot,
    ) -> tuple[ResolvedEvent, ...]:
        """Return committed events from the latest reachable scene boundary."""
        if not self.configured or snapshot.checkpoint_id is None:
            return snapshot.pending_scene_events
        kernel = self.kernel(snapshot.project_id)
        records = kernel.logs.reachable(kernel.checkpoints, snapshot.checkpoint_id)
        start = 0
        for index, record in enumerate(records):
            if record.result.boundary != SimulationBoundary.NONE:
                start = index
        events: dict[str, ResolvedEvent] = {}
        for record in records[start:]:
            if record.result.resolved_turn is None:
                continue
            for event in record.result.resolved_turn.events:
                events[event.event_id] = event
        return tuple(events.values())

    def persist(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        status: TurnSessionStatus | None = None,
        restoration_notice_text: str | None = None,
    ) -> SessionManifest | None:
        if self._commit_kernel_factory is None:
            return None
        manifest = SessionManifest.from_snapshot(
            snapshot,
            status=status,
            restoration_notice_text=restoration_notice_text,
        )
        self.kernel(snapshot.project_id).sessions.save(manifest)
        return manifest

    def publish(
        self,
        snapshot: TurnSessionSnapshot,
        event_type: EngineEventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        self.persist(snapshot)
        self.event_bus.publish(
            project_id=snapshot.project_id,
            subject_id=snapshot.session_id,
            event_type=event_type,
            payload=payload or {"session_id": snapshot.session_id},
        )

    def publish_stage(self, event: SimulationStageEvent) -> None:
        if event.status == StageStatus.RUNNING and self._last_started_step.get(
            event.session_id
        ) != event.step:
            self._last_started_step[event.session_id] = event.step
            self.event_bus.publish(
                project_id=event.project_id,
                subject_id=event.session_id,
                event_type="simulation.step.started",
                payload={
                    "session_id": event.session_id,
                    "branch_id": event.branch_id,
                    "step": event.step,
                },
            )
        if event.status == StageStatus.RUNNING:
            event_type: EngineEventType = "simulation.stage.started"
        elif event.status == StageStatus.FAILED:
            event_type = "simulation.stage.failed"
        else:
            event_type = "simulation.stage.completed"
        self.event_bus.publish(
            project_id=event.project_id,
            subject_id=event.session_id,
            event_type=event_type,
            payload=event.model_dump(mode="json"),
        )

    def start(
        self,
        request: TurnSessionRequest,
        *,
        observer: SimulationObserver,
    ) -> TurnSessionSnapshot:
        snapshot = self.engine.create_session(request)
        self.engine.attach_observer(snapshot.session_id, observer)
        if self._commit_kernel_factory is not None:
            try:
                committed = self.kernel(snapshot.project_id).save_checkpoint(
                    snapshot,
                    reason="session created",
                    genesis_memory_delta=self.engine.pending_memory_records(
                        snapshot.session_id
                    ),
                )
                self.engine.mark_memory_committed(snapshot.session_id)
                snapshot = self.engine.attach_checkpoint(
                    snapshot.session_id,
                    committed.checkpoint_id,
                    committed.history_head_id,
                )
                self.publish(snapshot, "simulation.checkpointed")
            except Exception as error:
                self.engine.fail(snapshot.session_id, reason_text=str(error))
                raise
        self.publish(snapshot, "simulation.started")
        return snapshot

    def get_durable(
        self,
        project_id: str,
        session_id: str,
        *,
        observer: SimulationObserver,
    ) -> TurnSessionSnapshot | SessionManifest:
        try:
            snapshot = self.engine.get(session_id)
        except SessionNotFoundError:
            pass
        else:
            if snapshot.project_id != project_id:
                raise FileNotFoundError(session_id)
            return snapshot

        manifest = self.kernel(project_id).sessions.load(session_id)
        if manifest.project_id != project_id:
            raise FileNotFoundError(session_id)
        if manifest.head_checkpoint_id is None:
            interrupted = manifest.model_copy(
                update={
                    "status": TurnSessionStatus.INTERRUPTED,
                    "restoration_notice_text": (
                        f"上一次运行在 Step {manifest.current_step} 被中断, "
                        "且没有可恢复的检查点。"
                    ),
                }
            )
            self.kernel(project_id).sessions.save(interrupted)
            return interrupted

        loaded = self.kernel(project_id).load_checkpoint(
            project_id,
            manifest.head_checkpoint_id,
        )
        if loaded.status in {
            TurnSessionStatus.TERMINATED,
            TurnSessionStatus.CANCELLED,
            TurnSessionStatus.FAILED,
        }:
            return loaded
        notice = f"上一次运行被中断, 已恢复到 Step {loaded.current_step} 的检查点。"
        snapshot = self.engine.restore(loaded)
        self.engine.attach_observer(snapshot.session_id, observer)
        snapshot = self.engine.set_restoration_notice(snapshot.session_id, notice)
        self.publish(
            snapshot,
            "simulation.started",
            payload={
                "session_id": snapshot.session_id,
                "branch_id": snapshot.branch_id,
                "checkpoint_id": manifest.head_checkpoint_id,
                "restored": True,
                "step": snapshot.current_step,
                "status": snapshot.status.value,
                "restoration_notice_text": notice,
            },
        )
        return snapshot

    def list(self, project_id: str) -> tuple[SessionManifest, ...]:
        if self._commit_kernel_factory is None:
            return tuple(
                SessionManifest.from_snapshot(snapshot)
                for snapshot in self.engine.list_snapshots()
                if snapshot.project_id == project_id
            )
        store = self.kernel(project_id).sessions
        live_session_ids = {
            snapshot.session_id
            for snapshot in self.engine.list_snapshots()
            if snapshot.project_id == project_id
        }
        manifests = []
        for manifest in store.list(project_id):
            if (
                manifest.status == TurnSessionStatus.RUNNING
                and manifest.session_id not in live_session_ids
            ):
                manifest = manifest.model_copy(
                    update={
                        "status": TurnSessionStatus.INTERRUPTED,
                        "restoration_notice_text": (
                            f"上一次运行在 Step {manifest.current_step} 被中断; "
                            "打开会话后将恢复最后检查点。"
                        ),
                    }
                )
                store.save(manifest)
            manifests.append(manifest)
        return tuple(manifests)

    def restore(
        self,
        project_id: str,
        *,
        checkpoint_id: str,
        observer: SimulationObserver,
    ) -> TurnSessionSnapshot:
        loaded = self.kernel(project_id).load_checkpoint(project_id, checkpoint_id)
        snapshot = self.engine.restore(
            loaded.model_copy(update={"checkpoint_id": checkpoint_id})
        )
        self.engine.attach_observer(snapshot.session_id, observer)
        snapshot = self.engine.set_restoration_notice(
            snapshot.session_id,
            f"已恢复到 Step {snapshot.current_step} 的检查点。",
        )
        self.publish(
            snapshot,
            "simulation.started",
            payload={
                "session_id": snapshot.session_id,
                "branch_id": snapshot.branch_id,
                "checkpoint_id": checkpoint_id,
                "restored": True,
                "step": snapshot.current_step,
                "status": snapshot.status.value,
            },
        )
        return snapshot

    def restore_branch(
        self,
        project_id: str,
        *,
        branch_id: str,
        observer: SimulationObserver,
    ) -> TurnSessionSnapshot:
        """Load a branch head without changing its immutable source checkpoint."""
        kernel = self.kernel(project_id)
        branch = kernel.branches.load(branch_id)
        if branch.project_id != project_id:
            raise FileNotFoundError(branch_id)
        for snapshot in self.engine.list_snapshots():
            if (
                snapshot.project_id == project_id
                and snapshot.branch_id == branch_id
                and snapshot.status
                in {
                    TurnSessionStatus.CREATED,
                    TurnSessionStatus.RUNNING,
                    TurnSessionStatus.PAUSED,
                }
            ):
                return snapshot
        if branch.head_checkpoint_id is None:
            raise ValueError("branch has no checkpoint to restore")

        source = kernel.load_checkpoint(project_id, branch.head_checkpoint_id)
        if source.branch_id != branch_id or source.status in {
            TurnSessionStatus.TERMINATED,
            TurnSessionStatus.CANCELLED,
            TurnSessionStatus.FAILED,
        }:
            rebound_request = source.request.model_copy(
                update={"branch_id": branch_id}
            )
            updates = {
                "branch_id": branch_id,
                "request": rebound_request,
                "checkpoint_id": branch.head_checkpoint_id,
                "state_hash": "0" * 64,
            }
            if source.status in {
                TurnSessionStatus.TERMINATED,
                TurnSessionStatus.CANCELLED,
                TurnSessionStatus.FAILED,
            }:
                updates.update(
                    {
                        "status": TurnSessionStatus.PAUSED,
                        "pending_control": PendingControl.NONE,
                        "termination_reason_text": None,
                        "restoration_notice_text": None,
                    }
                )
            rebound = source.model_copy(
                update={
                    "session_id": f"session:{uuid.uuid4().hex}",
                    **updates,
                }
            )
            if source.branch_id == branch_id:
                rebound = rebound.model_copy(update={"session_id": source.session_id})
            source = rebound.model_copy(
                update={"state_hash": calculate_snapshot_state_hash(rebound)}
            )

        snapshot = self.engine.restore(
            source.model_copy(update={"checkpoint_id": branch.head_checkpoint_id})
        )
        self.engine.attach_observer(snapshot.session_id, observer)
        snapshot = self.engine.set_restoration_notice(
            snapshot.session_id,
            f"已恢复到 Step {snapshot.current_step} 的分支检查点。",
        )
        self.publish(
            snapshot,
            "simulation.started",
            payload={
                "session_id": snapshot.session_id,
                "branch_id": branch_id,
                "checkpoint_id": branch.head_checkpoint_id,
                "restored": True,
                "step": snapshot.current_step,
                "status": snapshot.status.value,
            },
        )
        return snapshot

    def read_branch(
        self,
        project_id: str,
        *,
        branch_id: str,
    ) -> TurnSessionSnapshot:
        """Read the committed branch head without restoring or publishing it."""
        kernel = self.kernel(project_id)
        branch = kernel.branches.load(branch_id)
        if branch.project_id != project_id:
            raise FileNotFoundError(branch_id)
        if branch.head_checkpoint_id is None:
            raise ValueError("branch has no checkpoint")

        source = kernel.load_checkpoint(project_id, branch.head_checkpoint_id)
        if source.branch_id == branch_id:
            return source
        rebound = source.model_copy(
            update={
                "branch_id": branch_id,
                "request": source.request.model_copy(update={"branch_id": branch_id}),
                "state_hash": "0" * 64,
            }
        )
        return rebound.model_copy(
            update={"state_hash": calculate_snapshot_state_hash(rebound)}
        )

    def trace_for(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
        *,
        status: ModelCallStatus = ModelCallStatus.SUCCEEDED,
    ) -> TurnTrace:
        stage_events = self.engine.drain_stage_events(result.session_id)
        model_calls = self.engine.drain_model_traces(result.session_id)
        completed_stages = tuple(
            event for event in stage_events if event.status != StageStatus.RUNNING
        )
        stages = tuple(
            StageTrace(
                stage_id=(f"stage:{event.session_id}:{event.step}:{event.stage.value}"),
                stage_type=event.stage.value,
                started_at=event.started_at,
                completed_at=event.completed_at,
                status=event.status,
                actor_id=event.actor_id,
                action_spec=event.action_spec,
                model_call_ids=tuple(
                    call.call_id
                    for call in model_calls
                    if call.step == event.step
                    and call.started_at >= event.started_at
                    and (
                        event.completed_at is None
                        or call.started_at <= event.completed_at
                    )
                ),
                input_record_ids=event.input_record_ids,
                output_record_ids=event.output_record_ids,
                visible_to=event.visible_to,
                prompt_tokens=event.prompt_tokens,
                completion_tokens=event.completion_tokens,
                duration_ms=event.duration_ms,
                checkpoint_id=event.checkpoint_id,
                error_code=event.error_code,
                detail_text=event.summary_text,
            )
            for event in completed_stages
        )
        now = datetime.now(UTC)
        started_at = min((event.started_at for event in stage_events), default=now)
        completed_at = max(
            (
                event.completed_at
                for event in completed_stages
                if event.completed_at is not None
            ),
            default=now,
        )
        return TurnTrace(
            trace_id=f"trace:{uuid.uuid4().hex}",
            session_id=result.session_id,
            branch_id=result.branch_id,
            step=result.step,
            content_locale=snapshot.content_locale,
            stages=stages,
            model_calls=model_calls,
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
            started_at=started_at,
            completed_at=completed_at,
            status=status,
        )

    def record_failure(self, session_id: str) -> None:
        if self._commit_kernel_factory is None:
            return
        snapshot = self.engine.get(session_id)
        result = StepResult(
            session_id=session_id,
            branch_id=snapshot.branch_id,
            step=snapshot.current_step,
            acting_actor_id=None,
            action_spec=None,
            action_text=None,
            resolved_turn=None,
            status=TurnSessionStatus.FAILED,
        )
        trace = self.trace_for(result, snapshot, status=ModelCallStatus.FAILED)
        try:
            self.kernel(snapshot.project_id).append_step(
                result,
                snapshot,
                trace,
                checkpoint=False,
            )
        except (OSError, ValueError):
            return

    def commit_step(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
        *,
        command_receipt: CommandReceiptCommit | None = None,
    ) -> tuple[StepResult, TurnSessionSnapshot, CommitResult | None]:
        trace = self.trace_for(result, snapshot)
        snapshot = self.engine.get(result.session_id)
        result = result.model_copy(update={"status": snapshot.status})
        memory_delta = self.engine.pending_memory_records(result.session_id)
        if self._commit_kernel_factory is None:
            self.engine.mark_memory_committed(result.session_id)
            return result, snapshot, None
        commit_started = datetime.now(UTC)
        self.publish_stage(
            SimulationStageEvent(
                event_id=f"stage-event:{uuid.uuid4().hex}",
                project_id=snapshot.project_id,
                session_id=snapshot.session_id,
                branch_id=snapshot.branch_id,
                step=result.step,
                stage=SimulationStage.COMMIT,
                status=StageStatus.RUNNING,
                started_at=commit_started,
            )
        )
        try:
            trace = trace.model_copy(
                update={
                    "stages": (
                        *trace.stages,
                        StageTrace(
                            stage_id=(
                                f"stage:{snapshot.session_id}:{result.step}:commit"
                            ),
                            stage_type=SimulationStage.COMMIT.value,
                            started_at=commit_started,
                            completed_at=datetime.now(UTC),
                            status=StageStatus.SUCCEEDED,
                            input_record_ids=(
                                f"event:{result.session_id}:{result.step}",
                            )
                            if result.resolved_turn is not None
                            else (),
                            detail_text="Step log and checkpoint commit",
                            duration_ms=max(
                                0,
                                int(
                                    (datetime.now(UTC) - commit_started).total_seconds()
                                    * 1000
                                ),
                            ),
                        ),
                    ),
                    "completed_at": datetime.now(UTC),
                }
            )
            committed = self.kernel(snapshot.project_id).append_step(
                result,
                snapshot,
                trace,
                memory_delta=memory_delta,
                checkpoint=True,
                command_receipt=command_receipt,
            )
            self.engine.mark_memory_committed(result.session_id)
            if committed is not None:
                snapshot = self.engine.attach_checkpoint(
                    snapshot.session_id,
                    committed.checkpoint_id,
                    committed.history_head_id,
                )
                result = result.model_copy(
                    update={"checkpoint_id": committed.checkpoint_id}
                )
            else:
                result = result.model_copy(
                    update={"checkpoint_id": snapshot.checkpoint_id}
                )
            commit_completed = datetime.now(UTC)
            # The AtomicBatch above is the authoritative boundary. Observability
            # notifications happen after it and cannot invalidate the committed
            # step or its command receipt when an event sink is unavailable.
            with suppress(Exception):
                self.publish_stage(
                    SimulationStageEvent(
                        event_id=f"stage-event:{uuid.uuid4().hex}",
                        project_id=snapshot.project_id,
                        session_id=snapshot.session_id,
                        branch_id=snapshot.branch_id,
                        step=result.step,
                        stage=SimulationStage.COMMIT,
                        status=StageStatus.SUCCEEDED,
                        summary_text=(
                            "Step log and checkpoint committed"
                            if committed is not None
                            else "Step log committed; checkpoint interval not reached"
                        ),
                        checkpoint_id=(
                            committed.checkpoint_id if committed is not None else None
                        ),
                        duration_ms=max(
                            0,
                            int(
                                (commit_completed - commit_started).total_seconds()
                                * 1000
                            ),
                        ),
                        started_at=commit_started,
                        completed_at=commit_completed,
                    )
                )
            if committed is not None:
                with suppress(Exception):
                    self.publish(
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
            commit_completed = datetime.now(UTC)
            self.publish_stage(
                SimulationStageEvent(
                    event_id=f"stage-event:{uuid.uuid4().hex}",
                    project_id=snapshot.project_id,
                    session_id=snapshot.session_id,
                    branch_id=snapshot.branch_id,
                    step=result.step,
                    stage=SimulationStage.COMMIT,
                    status=StageStatus.FAILED,
                    summary_text=str(error),
                    duration_ms=max(
                        0,
                        int((commit_completed - commit_started).total_seconds() * 1000),
                    ),
                    error_code=type(error).__name__.lower(),
                    started_at=commit_started,
                    completed_at=commit_completed,
                )
            )
            failed = self.engine.fail(snapshot.session_id, reason_text=str(error))
            self.publish(
                failed,
                "simulation.failed",
                payload={"session_id": failed.session_id, "reason": str(error)},
            )
            raise

    def checkpoint_inactive_transition(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        reason: str,
    ) -> TurnSessionSnapshot:
        if not self.configured or snapshot.status == TurnSessionStatus.RUNNING:
            return snapshot
        self.checkpoint(snapshot.session_id, reason=reason)
        return self.engine.get(snapshot.session_id)

    def checkpoint(self, session_id: str, *, reason: str) -> CommitResult:
        snapshot = self.engine.get(session_id)
        if snapshot.status == TurnSessionStatus.RUNNING:
            raise RuntimeError("checkpoint requires a completed step boundary")
        committed = self.kernel(snapshot.project_id).save_checkpoint(
            snapshot,
            reason=reason,
        )
        snapshot = self.engine.attach_checkpoint(
            session_id,
            committed.checkpoint_id,
            committed.history_head_id,
        )
        self.publish(
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
        snapshot = self.engine.switch_locale(session_id, content_locale=content_locale)
        if self.configured:
            committed = self.kernel(snapshot.project_id).save_checkpoint(
                snapshot,
                reason="content locale changed",
            )
            snapshot = self.engine.attach_checkpoint(
                session_id,
                committed.checkpoint_id,
                committed.history_head_id,
            )
            self.publish(snapshot, "simulation.checkpointed")
        return snapshot

    def checkpoint_inactive_sessions(self) -> tuple[CommitResult, ...]:
        if not self.configured:
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
