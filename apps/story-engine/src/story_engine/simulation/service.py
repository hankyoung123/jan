import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from threading import Event, RLock, Thread

from pydantic import JsonValue

from story_engine.domain.session_manifest import SessionManifest
from story_engine.domain.simulation import (
    CommitResult,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import (
    ModelCallStatus,
    SimulationStage,
    SimulationStageEvent,
    StageStatus,
    StageTrace,
    TurnTrace,
)
from story_engine.events.stream import EngineEventBus, EngineEventType
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.simulation.engine import SessionNotFoundError, StoryTurnEngine
from story_engine.simulation.output import BoundaryOutputCoordinator
from story_engine.simulation.session import calculate_snapshot_state_hash

CommitKernelFactory = Callable[[str], SimulationCommitKernel]
BoundaryOutputFactory = Callable[[str], BoundaryOutputCoordinator]


class SimulationApplicationService:
    def __init__(
        self,
        engine: StoryTurnEngine,
        event_bus: EngineEventBus,
        commit_kernel_factory: CommitKernelFactory | None = None,
        boundary_output_factory: BoundaryOutputFactory | None = None,
    ) -> None:
        self.engine = engine
        self.event_bus = event_bus
        self._commit_kernel_factory = commit_kernel_factory
        self._boundary_output_factory = boundary_output_factory
        self._run_threads: dict[str, Thread] = {}
        self._run_cancellations: dict[str, Event] = {}
        self._run_lock = RLock()

    def _kernel(self, project_id: str) -> SimulationCommitKernel:
        if self._commit_kernel_factory is None:
            raise RuntimeError("simulation persistence is not configured")
        return self._commit_kernel_factory(project_id)

    def _persist(
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
        self._kernel(snapshot.project_id).sessions.save(manifest)
        return manifest

    def _publish(
        self,
        snapshot: TurnSessionSnapshot,
        event_type: EngineEventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        self._persist(snapshot)
        self.event_bus.publish(
            project_id=snapshot.project_id,
            subject_id=snapshot.session_id,
            event_type=event_type,
            payload=payload or {"session_id": snapshot.session_id},
        )

    def publish(self, event: SimulationStageEvent) -> None:
        if (
            event.stage == SimulationStage.TERMINATION
            and event.status == StageStatus.RUNNING
        ):
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

    def start(self, request: TurnSessionRequest) -> TurnSessionSnapshot:
        snapshot = self.engine.create_session(request)
        self.engine.attach_observer(snapshot.session_id, self)
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

    def get_durable(
        self,
        project_id: str,
        session_id: str,
    ) -> TurnSessionSnapshot | SessionManifest:
        try:
            snapshot = self.engine.get(session_id)
        except SessionNotFoundError:
            pass
        else:
            if snapshot.project_id != project_id:
                raise FileNotFoundError(session_id)
            return snapshot

        manifest = self._kernel(project_id).sessions.load(session_id)
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
            self._kernel(project_id).sessions.save(interrupted)
            return interrupted

        loaded = self._kernel(project_id).load_checkpoint(
            project_id,
            manifest.head_checkpoint_id,
        )
        if manifest.status in {
            TurnSessionStatus.TERMINATED,
            TurnSessionStatus.CANCELLED,
            TurnSessionStatus.FAILED,
        }:
            archived = loaded.model_copy(
                update={
                    "status": manifest.status,
                    "checkpoint_id": manifest.head_checkpoint_id,
                    "updated_at": manifest.updated_at,
                    "termination_reason_text": manifest.termination_reason_text,
                    "restoration_notice_text": manifest.restoration_notice_text,
                    "state_hash": "0" * 64,
                }
            )
            return archived.model_copy(
                update={"state_hash": calculate_snapshot_state_hash(archived)}
            )
        interrupted_step = manifest.current_step
        checkpoint_step = loaded.current_step
        notice = (
            f"上一次运行在 Step {interrupted_step} 被中断, "
            f"已恢复到 Step {checkpoint_step} 的检查点。"
        )
        snapshot = self.engine.restore(loaded)
        self.engine.attach_observer(snapshot.session_id, self)
        snapshot = self.engine.set_restoration_notice(snapshot.session_id, notice)
        self._publish(
            snapshot,
            "simulation.started",
            payload={
                "session_id": snapshot.session_id,
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
        store = self._kernel(project_id).sessions
        live_session_ids: set[str] = set()
        for snapshot in self.engine.list_snapshots():
            if snapshot.project_id == project_id:
                live_session_ids.add(snapshot.session_id)
                store.save(SessionManifest.from_snapshot(snapshot))
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
    ) -> TurnSessionSnapshot:
        if self._commit_kernel_factory is None:
            raise RuntimeError("simulation persistence is not configured")
        loaded = self._commit_kernel_factory(project_id).load_checkpoint(
            project_id,
            checkpoint_id,
        )
        snapshot = self.engine.restore(
            loaded.model_copy(update={"checkpoint_id": checkpoint_id})
        )
        self.engine.attach_observer(snapshot.session_id, self)
        snapshot = self.engine.set_restoration_notice(
            snapshot.session_id,
            f"已恢复到 Step {snapshot.current_step} 的检查点。",
        )
        self._publish(
            snapshot,
            "simulation.started",
            payload={
                "session_id": snapshot.session_id,
                "checkpoint_id": checkpoint_id,
                "restored": True,
                "step": snapshot.current_step,
                "status": snapshot.status.value,
            },
        )
        return snapshot

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
        started_at = min(
            (event.started_at for event in stage_events),
            default=now,
        )
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

    def _record_failure(self, session_id: str) -> None:
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
        trace = self._trace_for(
            result,
            snapshot,
            status=ModelCallStatus.FAILED,
        )
        try:
            self._commit_kernel_factory(snapshot.project_id).append_step(
                result,
                snapshot,
                trace,
                checkpoint=False,
            )
        except (OSError, ValueError):
            # The original runtime failure remains authoritative if trace IO fails.
            return

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
        commit_started = datetime.now(UTC)
        self.publish(
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
            checkpoint_due = (
                snapshot.current_step % snapshot.request.control.checkpoint_every_steps
                == 0
                or snapshot.status == TurnSessionStatus.TERMINATED
                or result.boundary.value != "none"
            )
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
                            detail_text=(
                                "Step log and checkpoint commit"
                                if checkpoint_due
                                else "Step log commit"
                            ),
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
            committed = self._commit_kernel_factory(snapshot.project_id).append_step(
                result,
                snapshot,
                trace,
                checkpoint=checkpoint_due,
            )
            if committed is not None:
                snapshot = self.engine.attach_checkpoint(
                    snapshot.session_id,
                    committed.checkpoint_id,
                )
                result = result.model_copy(
                    update={"checkpoint_id": committed.checkpoint_id}
                )
            else:
                result = result.model_copy(
                    update={"checkpoint_id": snapshot.checkpoint_id}
                )
            commit_completed = datetime.now(UTC)
            self.publish(
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
                        int((commit_completed - commit_started).total_seconds() * 1000),
                    ),
                    started_at=commit_started,
                    completed_at=commit_completed,
                )
            )
            if committed is not None:
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
            commit_completed = datetime.now(UTC)
            self.publish(
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
            self._publish(
                failed,
                "simulation.failed",
                payload={"session_id": failed.session_id, "reason": str(error)},
            )
            raise

    def step(self, session_id: str, *, cancellation: Event) -> StepResult:
        try:
            result = self.engine.step(session_id, cancellation=cancellation)
        except Exception as error:
            snapshot = self.engine.get(session_id)
            if snapshot.status == TurnSessionStatus.FAILED:
                self._record_failure(session_id)
                self._publish(
                    snapshot,
                    "simulation.failed",
                    payload={"session_id": session_id, "reason": str(error)},
                )
            raise
        snapshot = self.engine.get(session_id)
        result, snapshot, _ = self._commit_step(result, snapshot)
        self._process_boundary_outputs(result, snapshot)
        self._step_sink(lambda: snapshot)(result)
        if snapshot.status == TurnSessionStatus.PAUSED:
            self._publish(snapshot, "simulation.paused")
        elif snapshot.status == TurnSessionStatus.TERMINATED:
            self._publish(snapshot, "simulation.terminated")
        return result

    def run(self, session_id: str, *, cancellation: Event) -> TurnSessionSnapshot:
        def commit_and_publish(result: StepResult) -> None:
            if result.status == TurnSessionStatus.CANCELLED:
                return
            snapshot = self.engine.get(session_id)
            committed_result, committed_snapshot, _ = self._commit_step(
                result,
                snapshot,
            )
            self._process_boundary_outputs(committed_result, committed_snapshot)
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

    def run_in_background(
        self,
        session_id: str,
        *,
        resume: bool = False,
    ) -> TurnSessionSnapshot:
        with self._run_lock:
            existing = self._run_threads.get(session_id)
            if existing is not None and existing.is_alive():
                raise RuntimeError("session already has a background run")
            accepted = self.engine.prepare_run(
                session_id,
                require_paused=resume,
            )
            cancellation = Event()
            self._persist(accepted)

            def execute() -> None:
                try:
                    self.run(session_id, cancellation=cancellation)
                except Exception as error:
                    snapshot = self.engine.get(session_id)
                    if snapshot.status == TurnSessionStatus.CANCELLED:
                        return
                    if snapshot.status not in {
                        TurnSessionStatus.FAILED,
                        TurnSessionStatus.TERMINATED,
                    }:
                        snapshot = self.engine.fail(
                            session_id,
                            reason_text=str(error),
                        )
                    self._record_failure(session_id)
                    self._publish(
                        snapshot,
                        "simulation.failed",
                        payload={
                            "session_id": session_id,
                            "reason": str(error),
                        },
                    )
                finally:
                    with self._run_lock:
                        self._run_threads.pop(session_id, None)
                        self._run_cancellations.pop(session_id, None)

            thread = Thread(
                target=execute,
                name=f"simulation-run-{session_id}",
            )
            self._run_threads[session_id] = thread
            self._run_cancellations[session_id] = cancellation
            thread.start()
            return accepted

    def pause(self, session_id: str) -> TurnSessionSnapshot:
        snapshot = self.engine.pause(session_id)
        event_type: EngineEventType = (
            "simulation.pause.requested"
            if snapshot.status == TurnSessionStatus.RUNNING
            else "simulation.paused"
        )
        self._publish(snapshot, event_type)
        return snapshot

    def terminate(self, session_id: str, *, reason_text: str) -> TurnSessionSnapshot:
        snapshot = self.engine.terminate(session_id, reason_text=reason_text)
        event_type: EngineEventType = (
            "simulation.termination.requested"
            if snapshot.status == TurnSessionStatus.RUNNING
            else "simulation.terminated"
        )
        self._publish(snapshot, event_type)
        return snapshot

    def cancel(self, session_id: str, *, reason_text: str) -> TurnSessionSnapshot:
        with self._run_lock:
            cancellation = self._run_cancellations.get(session_id)
            if cancellation is not None:
                cancellation.set()
        snapshot = self.engine.cancel(session_id, reason_text=reason_text)
        self._publish(
            snapshot,
            "simulation.terminated",
            payload={
                "session_id": session_id,
                "status": snapshot.status.value,
                "reason": reason_text,
            },
        )
        return snapshot

    def shutdown(self) -> None:
        with self._run_lock:
            interrupted_session_ids = tuple(self._run_threads)
            cancellations = tuple(self._run_cancellations.values())
            threads = tuple(self._run_threads.values())
        for cancellation in cancellations:
            cancellation.set()
        for thread in threads:
            thread.join(timeout=5)
        try:
            self.checkpoint_inactive_sessions()
        finally:
            self.engine.cancel_all()
            for session_id in (
                interrupted_session_ids
                if self._commit_kernel_factory is not None
                else ()
            ):
                snapshot = self.engine.get(session_id)
                notice = (
                    f"上一次运行在 Step {snapshot.current_step} 被中断; "
                    "打开会话后将恢复最后检查点。"
                )
                manifest = SessionManifest.from_snapshot(
                    snapshot,
                    status=TurnSessionStatus.INTERRUPTED,
                    restoration_notice_text=notice,
                ).model_copy(update={"termination_reason_text": None})
                self._kernel(snapshot.project_id).sessions.save(manifest)

    def checkpoint(self, session_id: str, *, reason: str) -> CommitResult:
        if self._commit_kernel_factory is None:
            raise RuntimeError("simulation persistence is not configured")
        snapshot = self.engine.get(session_id)
        if snapshot.status == TurnSessionStatus.RUNNING:
            raise RuntimeError("checkpoint requires a completed step boundary")
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

    def _process_boundary_outputs(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> None:
        if self._boundary_output_factory is None or result.boundary.value == "none":
            return
        self._boundary_output_factory(snapshot.project_id).process(result, snapshot)
