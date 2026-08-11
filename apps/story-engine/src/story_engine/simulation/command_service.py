"""Single-step and continuous simulation command execution."""

import hashlib
import uuid
from collections.abc import Callable
from threading import Event, RLock, Thread

from pydantic import JsonValue

from story_engine.domain.projection import SimulationBoundary
from story_engine.domain.simulation import (
    StepResult,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.events.stream import EngineEventType
from story_engine.persistence.command_store import CommandReceiptCommit
from story_engine.simulation.commands import SessionCommandCoordinator
from story_engine.simulation.engine import (
    InvalidSessionTransitionError,
    StoryTurnEngine,
)
from story_engine.simulation.persistence import SimulationPersistenceService
from story_engine.simulation.projection_coordinator import (
    SimulationProjectionCoordinator,
)


class SimulationCommandService:
    """Execute commands against the engine after persistence boundaries are set."""

    def __init__(
        self,
        engine: StoryTurnEngine,
        persistence: SimulationPersistenceService,
        projections: SimulationProjectionCoordinator,
        command_coordinator: SessionCommandCoordinator,
    ) -> None:
        self.engine = engine
        self.persistence = persistence
        self.projections = projections
        self._commands = command_coordinator
        self._run_threads: dict[str, Thread] = {}
        self._run_cancellations: dict[str, Event] = {}
        self._run_lock = RLock()
        self._shutting_down = False

    def _publish(
        self,
        snapshot: TurnSessionSnapshot,
        event_type: EngineEventType,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        self.persistence.publish(snapshot, event_type, payload)

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

    @staticmethod
    def _snapshot_getter(
        snapshot: TurnSessionSnapshot,
    ) -> Callable[[], TurnSessionSnapshot]:
        return lambda: snapshot

    def _commit_and_project_step(
        self,
        result: StepResult,
        *,
        command_receipt: CommandReceiptCommit | None = None,
    ) -> tuple[StepResult, TurnSessionSnapshot]:
        """Commit the core step before running best-effort derived work."""
        snapshot = self.engine.get(result.session_id)
        committed_result, committed_snapshot, _ = self.persistence.commit_step(
            result,
            snapshot,
            command_receipt=command_receipt,
        )
        self.projections.schedule(committed_result, committed_snapshot)
        try:
            self._step_sink(self._snapshot_getter(committed_snapshot))(committed_result)
            if committed_snapshot.status == TurnSessionStatus.PAUSED:
                self._publish(committed_snapshot, "simulation.paused")
            elif committed_snapshot.status == TurnSessionStatus.TERMINATED:
                self._publish(committed_snapshot, "simulation.terminated")
        except Exception:
            # The durable step and receipt are already committed at this point.
            pass
        return committed_result, committed_snapshot

    def _step(
        self,
        session_id: str,
        *,
        cancellation: Event,
        command_receipt: CommandReceiptCommit | None = None,
        human_intent: str | None = None,
        eligible_actor_ids: tuple[str, ...] | None = None,
        deferred_boundary: SimulationBoundary = SimulationBoundary.NONE,
    ) -> StepResult:
        with self._run_lock:
            if self._shutting_down:
                raise InvalidSessionTransitionError(
                    "simulation service is shutting down"
                )
            running = self._run_threads.get(session_id)
            if running is not None and running.is_alive():
                raise InvalidSessionTransitionError("session is already running")
        try:
            result = self.engine.advance_one_step(
                session_id,
                cancellation=cancellation,
                human_intent=human_intent,
                eligible_actor_ids=eligible_actor_ids,
                deferred_boundary=deferred_boundary,
            )
        except Exception as error:
            snapshot = self.engine.get(session_id)
            if snapshot.status == TurnSessionStatus.FAILED:
                self.persistence.record_failure(session_id)
                self._publish(
                    snapshot,
                    "simulation.failed",
                    payload={"session_id": session_id, "reason": str(error)},
                )
            raise
        committed_result, _ = self._commit_and_project_step(
            result,
            command_receipt=command_receipt,
        )
        return committed_result

    def _restore_cancelled_session(self, session_id: str) -> TurnSessionSnapshot:
        snapshot = self.engine.get(session_id)
        if not self.persistence.configured or snapshot.checkpoint_id is None:
            return snapshot
        checkpoint = self.persistence.kernel(snapshot.project_id).load_checkpoint(
            snapshot.project_id,
            snapshot.checkpoint_id,
        )
        return self.engine.restore_to_checkpoint(session_id, checkpoint)

    def step(
        self,
        session_id: str,
        *,
        command_id: str,
        expected_state_hash: str,
        cancellation: Event,
    ) -> StepResult:
        receipt = self._commands.receipt_commit(
            command_id=command_id,
            operation="step",
            expected_state_hash=expected_state_hash,
        )

        return self._commands.execute(
            session_id=session_id,
            command_id=command_id,
            operation="step",
            expected_state_hash=expected_state_hash,
            current_state=lambda: self.engine.get(session_id),
            command=lambda: self._step(
                session_id,
                cancellation=cancellation,
                command_receipt=receipt,
            ),
            record_receipt=not self.persistence.configured,
        )

    @staticmethod
    def _combine_interactive_turn_results(
        player_result: StepResult,
        npc_result: StepResult,
    ) -> StepResult:
        if player_result.resolved_turn is None:
            return npc_result
        if npc_result.resolved_turn is None:
            return player_result
        boundary = npc_result.boundary.merge(
            player_result.resolved_turn.boundary
        )
        resolved_turn = player_result.resolved_turn.model_copy(
            update={
                "raw_resolution_text": "\n".join(
                    (
                        player_result.resolved_turn.raw_resolution_text,
                        npc_result.resolved_turn.raw_resolution_text,
                    )
                ),
                "events": (
                    *player_result.resolved_turn.events,
                    *npc_result.resolved_turn.events,
                ),
                "effects": (
                    *player_result.resolved_turn.effects,
                    *npc_result.resolved_turn.effects,
                ),
                "boundary": boundary,
            }
        )
        return player_result.model_copy(
            update={
                "resolved_turn": resolved_turn,
                "status": npc_result.status,
                "boundary": boundary,
                "promotion_decisions": (
                    *player_result.promotion_decisions,
                    *npc_result.promotion_decisions,
                ),
                "checkpoint_id": npc_result.checkpoint_id
                or player_result.checkpoint_id,
                "follow_up_actor_ids": (),
            }
        )

    def _interactive_expected_hash(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        command_id: str,
    ) -> str:
        if self.persistence.configured:
            receipt = self.persistence.kernel(snapshot.project_id).receipts.load(
                session_id=snapshot.session_id,
                command_id=command_id,
            )
            if receipt is not None:
                return str(receipt["expected_state_hash"])
        return snapshot.state_hash

    @staticmethod
    def _npc_handoff_command_id(player_checkpoint_id: str) -> str:
        return f"interactive-npc:{player_checkpoint_id}"

    @staticmethod
    def _reserved_npc_actor_ids(
        snapshot: TurnSessionSnapshot,
        player_result: StepResult,
    ) -> tuple[str, ...]:
        roster_actor_ids = set(snapshot.roster_actor_ids)
        return tuple(
            actor_id
            for actor_id in player_result.follow_up_actor_ids
            if actor_id in roster_actor_ids
            and actor_id != snapshot.player_actor_id
        )

    def _interactive_step(
        self,
        session_id: str,
        *,
        command_id: str,
        operation: str,
        expected_state_hash: str,
        human_intent: str | None = None,
        eligible_actor_ids: tuple[str, ...] | None = None,
        deferred_boundary: SimulationBoundary = SimulationBoundary.NONE,
    ) -> StepResult:
        receipt = self._commands.receipt_commit(
            command_id=command_id,
            operation=operation,
            expected_state_hash=expected_state_hash,
        )
        return self._commands.execute(
            session_id=session_id,
            command_id=command_id,
            operation=operation,
            expected_state_hash=expected_state_hash,
            current_state=lambda: self.engine.get(session_id),
            command=lambda: self._step(
                session_id,
                cancellation=Event(),
                command_receipt=receipt,
                human_intent=human_intent,
                eligible_actor_ids=eligible_actor_ids,
                deferred_boundary=deferred_boundary,
            ),
            record_receipt=not self.persistence.configured,
        )

    def _recover_interactive_step(
        self,
        session_id: str,
        snapshot: TurnSessionSnapshot,
    ) -> None:
        restored = self.engine.restore_to_checkpoint(
            session_id,
            snapshot,
            reactivate=True,
        )
        if self.persistence.configured:
            self.persistence.persist(restored)

    def _execute_reserved_npc_handoff(
        self,
        snapshot: TurnSessionSnapshot,
        player_result: StepResult,
        *,
        command_id: str,
    ) -> StepResult | None:
        npc_actor_ids = self._reserved_npc_actor_ids(snapshot, player_result)
        if not npc_actor_ids or snapshot.status in {
            TurnSessionStatus.TERMINATED,
            TurnSessionStatus.CANCELLED,
            TurnSessionStatus.FAILED,
        }:
            return None
        try:
            return self._interactive_step(
                snapshot.session_id,
                command_id=command_id,
                operation="interactive_npc_step",
                expected_state_hash=snapshot.state_hash,
                eligible_actor_ids=npc_actor_ids,
                deferred_boundary=(
                    player_result.resolved_turn.boundary
                    if player_result.resolved_turn is not None
                    else SimulationBoundary.NONE
                ),
            )
        except Exception:
            self._recover_interactive_step(snapshot.session_id, snapshot)
            raise

    def resume_pending_interactive_handoff(
        self,
        snapshot: TurnSessionSnapshot,
    ) -> StepResult | None:
        """Complete a player handoff left at the durable branch head."""
        if (
            not self.persistence.configured
            or snapshot.checkpoint_id is None
            or snapshot.player_actor_id is None
        ):
            return None
        kernel = self.persistence.kernel(snapshot.project_id)
        records = kernel.logs.reachable(kernel.checkpoints, snapshot.checkpoint_id)
        if not records:
            return None
        head_record = records[-1]
        player_result = head_record.result
        if (
            head_record.log_id != snapshot.history_head_id
            or player_result.acting_actor_id != snapshot.player_actor_id
            or not player_result.follow_up_actor_ids
        ):
            return None
        return self._execute_reserved_npc_handoff(
            snapshot,
            player_result,
            command_id=self._npc_handoff_command_id(snapshot.checkpoint_id),
        )

    def interactive_turn(
        self,
        session_id: str,
        *,
        text: str,
        command_id: str | None = None,
    ) -> StepResult:
        """Commit player and NPC steps independently; combine only the UI result."""
        base_command_id = command_id or f"interactive:{uuid.uuid4().hex}"
        if len(base_command_id) > 116:
            raise ValueError("interactive command ID is too long")
        return self._interactive_turn_steps(
            session_id,
            text=text,
            command_id=base_command_id,
        )

    def _interactive_turn_steps(
        self,
        session_id: str,
        *,
        text: str,
        command_id: str,
    ) -> StepResult:
        starting = self.engine.get(session_id)
        player_command_id = f"{command_id}:player"
        player_expected_hash = self._interactive_expected_hash(
            starting,
            command_id=player_command_id,
        )
        try:
            player_result = self._interactive_step(
                session_id,
                command_id=player_command_id,
                operation=(
                    "interactive_player_step:"
                    + hashlib.sha256(text.encode()).hexdigest()
                ),
                expected_state_hash=player_expected_hash,
                human_intent=text,
            )
        except Exception:
            self._recover_interactive_step(session_id, starting)
            raise
        if player_result.checkpoint_id is None or not self.persistence.configured:
            snapshot = self.engine.get(session_id)
        else:
            snapshot = self.persistence.kernel(starting.project_id).load_checkpoint(
                starting.project_id,
                player_result.checkpoint_id,
            )
        npc_command_id = (
            self._npc_handoff_command_id(player_result.checkpoint_id)
            if player_result.checkpoint_id is not None
            else f"{command_id}:npc"
        )
        npc_result = self._execute_reserved_npc_handoff(
            snapshot,
            player_result,
            command_id=npc_command_id,
        )
        if npc_result is None:
            return player_result
        return self._combine_interactive_turn_results(player_result, npc_result)

    def run(self, session_id: str, *, cancellation: Event) -> TurnSessionSnapshot:
        if self.engine.get(session_id).status != TurnSessionStatus.RUNNING:
            with self._run_lock:
                if self._shutting_down:
                    raise InvalidSessionTransitionError(
                        "simulation service is shutting down"
                    )
            self.engine.begin_continuous(session_id)
        while True:
            result = self.engine.advance_one_step(session_id, cancellation=cancellation)
            if result.status == TurnSessionStatus.CANCELLED:
                return self.engine.get(session_id)
            _, committed_snapshot = self._commit_and_project_step(result)
            if committed_snapshot.status != TurnSessionStatus.RUNNING:
                return committed_snapshot

    def run_in_background(
        self,
        session_id: str,
        *,
        command_id: str,
        expected_state_hash: str,
        resume: bool = False,
    ) -> TurnSessionSnapshot:
        return self._commands.execute(
            session_id=session_id,
            command_id=command_id,
            operation="resume" if resume else "run",
            expected_state_hash=expected_state_hash,
            current_state=lambda: self.engine.get(session_id),
            command=lambda: self._start_background_run(
                session_id,
                command_id=command_id,
                expected_state_hash=expected_state_hash,
                resume=resume,
            ),
        )

    def _start_background_run(
        self,
        session_id: str,
        *,
        command_id: str,
        expected_state_hash: str,
        resume: bool,
    ) -> TurnSessionSnapshot:
        with self._run_lock:
            if self._shutting_down:
                raise InvalidSessionTransitionError(
                    "simulation service is shutting down"
                )
            existing = self._run_threads.get(session_id)
            if existing is not None and existing.is_alive():
                raise RuntimeError("session already has a background run")
            accepted = self.engine.begin_continuous(session_id, require_paused=resume)
            self.persistence.persist(accepted)
            cancellation = Event()

            def execute() -> None:
                try:
                    self.run(session_id, cancellation=cancellation)
                except Exception as error:
                    snapshot = self.engine.get(session_id)
                    if snapshot.status == TurnSessionStatus.CANCELLED:
                        snapshot = self._restore_cancelled_session(session_id)
                        snapshot = self.persistence.checkpoint_inactive_transition(
                            snapshot,
                            reason="simulation cancelled",
                        )
                        self._publish(
                            snapshot,
                            "simulation.terminated",
                            payload={
                                "session_id": session_id,
                                "status": snapshot.status.value,
                                "reason": (
                                    snapshot.termination_reason_text or "cancelled"
                                ),
                            },
                        )
                        return
                    if snapshot.status not in {
                        TurnSessionStatus.FAILED,
                        TurnSessionStatus.TERMINATED,
                    }:
                        snapshot = self.engine.fail(session_id, reason_text=str(error))
                    self.persistence.record_failure(session_id)
                    self._publish(
                        snapshot,
                        "simulation.failed",
                        payload={"session_id": session_id, "reason": str(error)},
                    )
                finally:
                    with self._run_lock:
                        self._run_threads.pop(session_id, None)
                        self._run_cancellations.pop(session_id, None)

            thread = Thread(target=execute, name=f"simulation-run-{session_id}")
            self._run_threads[session_id] = thread
            self._run_cancellations[session_id] = cancellation
            thread.start()
            return accepted

    def pause(self, session_id: str) -> TurnSessionSnapshot:
        snapshot = self.engine.pause(session_id)
        snapshot = self.persistence.checkpoint_inactive_transition(
            snapshot,
            reason="simulation paused",
        )
        event_type: EngineEventType = (
            "simulation.pause.requested"
            if snapshot.status == TurnSessionStatus.RUNNING
            else "simulation.paused"
        )
        self._publish(snapshot, event_type)
        return snapshot

    def terminate(self, session_id: str, *, reason_text: str) -> TurnSessionSnapshot:
        snapshot = self.engine.terminate(session_id, reason_text=reason_text)
        snapshot = self.persistence.checkpoint_inactive_transition(
            snapshot,
            reason="simulation terminated",
        )
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
            run_thread = self._run_threads.get(session_id)
            if cancellation is not None:
                cancellation.set()
        snapshot = self.engine.cancel(session_id, reason_text=reason_text)
        if run_thread is None or not run_thread.is_alive():
            snapshot = self.persistence.checkpoint_inactive_transition(
                snapshot,
                reason="simulation cancelled",
            )
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
            if self._shutting_down:
                return
            self._shutting_down = True
            cancellations = tuple(self._run_cancellations.values())
            threads = tuple(self._run_threads.items())
        for cancellation in cancellations:
            cancellation.set()
        for _, thread in threads:
            thread.join(timeout=5)
        try:
            self.persistence.checkpoint_inactive_sessions()
        finally:
            with self._run_lock:
                live_threads = {
                    session_id: thread
                    for session_id, thread in self._run_threads.items()
                    if thread.is_alive()
                }
            self.engine.cancel_all()
            if self.persistence.configured:
                for session_id, thread in live_threads.items():
                    if not thread.is_alive():
                        continue
                    snapshot = self.engine.get(session_id)
                    notice = (
                        f"上一次运行在 Step {snapshot.current_step} 被中断; "
                        "打开会话后将恢复最后检查点。"
                    )
                    manifest = self.persistence.persist(
                        snapshot,
                        status=TurnSessionStatus.INTERRUPTED,
                        restoration_notice_text=notice,
                    )
                    if manifest is not None:
                        self.persistence.kernel(snapshot.project_id).sessions.save(
                            manifest.model_copy(
                                update={"termination_reason_text": None}
                            )
                        )
            self.projections.shutdown()
