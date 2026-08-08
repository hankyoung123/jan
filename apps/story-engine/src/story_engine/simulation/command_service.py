"""Single-step and continuous simulation command execution."""

import uuid
from collections.abc import Callable
from contextlib import suppress
from threading import Event, RLock, Thread

from pydantic import JsonValue

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
from story_engine.wiki.store import WikiStore


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

    def _initialize_promoted_character_wikis(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> None:
        if not self.persistence.configured or snapshot.checkpoint_id is None:
            return
        characters = {character.id: character for character in snapshot.characters}
        store = WikiStore(
            self.persistence.kernel(snapshot.project_id).root,
            snapshot.branch_id,
        )
        for decision in result.promotion_decisions:
            if not decision.promote:
                continue
            character = characters.get(decision.character_id)
            if character is None:
                raise ValueError("promoted character is missing from the snapshot")
            store.ensure_active_character_pages(
                character,
                checkpoint_id=snapshot.checkpoint_id,
                step=result.step,
                source_ids=decision.evidence_event_ids,
            )

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
        try:
            self._initialize_promoted_character_wikis(
                committed_result,
                committed_snapshot,
            )
        except Exception as error:
            with suppress(Exception):
                self.persistence.publish(
                    committed_snapshot,
                    "simulation.stage.failed",
                    payload={
                        "session_id": committed_snapshot.session_id,
                        "stage": "promoted_character_wiki",
                        "error": str(error),
                    },
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
        boundary = (
            npc_result.boundary
            if npc_result.boundary.value != "none"
            else player_result.boundary
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
            }
        )

    def interactive_turn(self, session_id: str, *, text: str) -> StepResult:
        """Commit a player intent, then one GM-selected NPC response when available."""
        state = self.engine.get(session_id)
        command_id = f"interactive:{uuid.uuid4().hex}"
        receipt = self._commands.receipt_commit(
            command_id=command_id,
            operation="interactive_turn",
            expected_state_hash=state.state_hash,
        )
        return self._commands.execute(
            session_id=session_id,
            command_id=command_id,
            operation="interactive_turn",
            expected_state_hash=state.state_hash,
            current_state=lambda: self.engine.get(session_id),
            command=lambda: self._interactive_turn_steps(
                session_id,
                text=text,
                receipt=receipt,
            ),
            record_receipt=not self.persistence.configured,
        )

    def _interactive_turn_steps(
        self,
        session_id: str,
        *,
        text: str,
        receipt: CommandReceiptCommit,
    ) -> StepResult:
        player_result = self._step(
            session_id,
            cancellation=Event(),
            command_receipt=receipt,
            human_intent=text,
        )
        snapshot = self.engine.get(session_id)
        npc_actor_ids = tuple(
            actor_id
            for actor_id in snapshot.roster_actor_ids
            if actor_id != snapshot.player_actor_id
        )
        if not npc_actor_ids or snapshot.status in {
            TurnSessionStatus.TERMINATED,
            TurnSessionStatus.CANCELLED,
            TurnSessionStatus.FAILED,
        }:
            return player_result
        npc_result = self._step(
            session_id,
            cancellation=Event(),
            eligible_actor_ids=npc_actor_ids,
        )
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
