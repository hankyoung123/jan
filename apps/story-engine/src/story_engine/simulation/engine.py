import time
import uuid
from collections.abc import Callable
from threading import Event, RLock

from story_engine.concordia_runtime.resolver import SimulationCancelledError
from story_engine.domain.simulation import (
    PendingControl,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import (
    ModelCallTrace,
    SimulationObserver,
    SimulationStageEvent,
)
from story_engine.simulation.control import hard_limit_reason, pauses_after_boundary
from story_engine.simulation.execution import SessionExecutionRegistry
from story_engine.simulation.runtime import StorySimulationRuntime
from story_engine.simulation.session import (
    SimulationSession,
    calculate_snapshot_state_hash,
)

RuntimeFactory = Callable[[str, TurnSessionRequest], StorySimulationRuntime]


class SessionNotFoundError(KeyError):
    """Raised when a session ID is unknown to this process."""


class InvalidSessionTransitionError(RuntimeError):
    """Raised when an operation is invalid for the current session state."""


class StoryTurnEngine:
    """Control-policy wrapper around Concordia's Sequential engine primitives."""

    def __init__(
        self,
        runtime_factory: RuntimeFactory,
        executions: SessionExecutionRegistry | None = None,
    ) -> None:
        self._runtime_factory = runtime_factory
        self._executions = executions or SessionExecutionRegistry()
        self._sessions: dict[str, SimulationSession] = {}
        self._lock = RLock()

    def _get(self, session_id: str) -> SimulationSession:
        with self._lock:
            try:
                return self._sessions[session_id]
            except KeyError as error:
                raise SessionNotFoundError(session_id) from error

    def create_session(self, request: TurnSessionRequest) -> TurnSessionSnapshot:
        session_id = f"session:{uuid.uuid4().hex}"
        self._executions.claim(request.project_id, request.branch_id, session_id)
        try:
            runtime = self._runtime_factory(session_id, request)
        except Exception:
            self._executions.release(
                request.project_id,
                request.branch_id,
                session_id,
            )
            raise
        initial_snapshot = getattr(runtime, "initial_snapshot", None)
        session = SimulationSession(
            session_id=session_id,
            request=request,
            runtime=runtime,
            current_step=(
                initial_snapshot.current_step if initial_snapshot is not None else 0
            ),
            completed_scenes=(
                initial_snapshot.completed_scenes if initial_snapshot is not None else 0
            ),
            raw_log_offset=(
                initial_snapshot.raw_log_offset if initial_snapshot is not None else 0
            ),
            total_model_tokens=(
                initial_snapshot.total_model_tokens
                if initial_snapshot is not None
                else 0
            ),
            consecutive_model_failures=(
                initial_snapshot.consecutive_model_failures
                if initial_snapshot is not None
                else 0
            ),
            checkpoint_id=(
                initial_snapshot.checkpoint_id if initial_snapshot is not None else None
            ),
        )
        with self._lock:
            self._sessions[session_id] = session
        return session.snapshot()

    def get(self, session_id: str) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            return session.snapshot()

    def list_snapshots(self) -> tuple[TurnSessionSnapshot, ...]:
        with self._lock:
            session_ids = tuple(self._sessions)
        return tuple(self.get(session_id) for session_id in session_ids)

    def attach_checkpoint(
        self,
        session_id: str,
        checkpoint_id: str,
    ) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            session.checkpoint_id = checkpoint_id
            return session.snapshot()

    def set_restoration_notice(
        self,
        session_id: str,
        notice_text: str | None,
    ) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            session.restoration_notice_text = notice_text
            session.touch()
            return session.snapshot()

    def attach_observer(
        self,
        session_id: str,
        observer: SimulationObserver,
    ) -> None:
        session = self._get(session_id)
        session.runtime.set_observer(observer)

    def drain_model_traces(self, session_id: str) -> tuple[ModelCallTrace, ...]:
        session = self._get(session_id)
        runtime = session.runtime
        drain = getattr(runtime, "drain_model_traces", None)
        traces: tuple[ModelCallTrace, ...] = () if drain is None else drain()
        with session.lock:
            session.total_model_tokens += sum(
                trace.prompt_tokens + trace.completion_tokens for trace in traces
            )
            if any(trace.status.value != "succeeded" for trace in traces):
                session.consecutive_model_failures += 1
            elif traces:
                session.consecutive_model_failures = 0
            token_limit_reached = (
                session.total_model_tokens >= session.request.control.max_total_tokens
            )
            failure_limit_reached = (
                session.consecutive_model_failures
                >= session.request.control.max_consecutive_model_failures
            )
            if token_limit_reached or failure_limit_reached:
                session.status = TurnSessionStatus.TERMINATED
                session.termination_reason_text = (
                    "maximum token budget reached"
                    if token_limit_reached
                    else "maximum consecutive model failures reached"
                )
                session.touch()
                self._executions.release(
                    session.request.project_id,
                    session.request.branch_id,
                    session.session_id,
                )
        return traces

    def drain_stage_events(
        self,
        session_id: str,
    ) -> tuple[SimulationStageEvent, ...]:
        session = self._get(session_id)
        return session.runtime.drain_stage_events()

    def fail(self, session_id: str, *, reason_text: str) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            session.status = TurnSessionStatus.FAILED
            session.termination_reason_text = reason_text
            session.touch()
            self._executions.release(
                session.request.project_id,
                session.request.branch_id,
                session.session_id,
            )
            return session.snapshot()

    def switch_locale(
        self,
        session_id: str,
        *,
        content_locale: str,
    ) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            self._ensure_user_override(session)
            if session.status not in {
                TurnSessionStatus.CREATED,
                TurnSessionStatus.PAUSED,
            }:
                raise InvalidSessionTransitionError(
                    "locale can change only at a created or paused step boundary"
                )
            session.runtime.set_content_locale(content_locale)
            session.request = session.request.model_copy(
                update={"content_locale": content_locale}
            )
            session.touch()
            return session.snapshot()

    @staticmethod
    def _ensure_active(session: SimulationSession) -> None:
        if session.status in {
            TurnSessionStatus.TERMINATED,
            TurnSessionStatus.CANCELLED,
            TurnSessionStatus.FAILED,
        }:
            raise InvalidSessionTransitionError(
                f"session is already {session.status.value}"
            )

    @classmethod
    def _ensure_can_advance(cls, session: SimulationSession) -> None:
        cls._ensure_active(session)

    @staticmethod
    def _ensure_user_override(session: SimulationSession) -> None:
        if not session.request.control.allow_user_override:
            raise InvalidSessionTransitionError(
                "session control policy does not allow user overrides"
            )

    def _execute_one(
        self,
        session: SimulationSession,
        *,
        cancellation: Event,
        human_intent: str | None = None,
        eligible_actor_ids: tuple[str, ...] | None = None,
    ) -> StepResult:
        if cancellation.is_set() or session.status == TurnSessionStatus.CANCELLED:
            session.runtime.cancellation.set()
        try:
            if human_intent is None:
                if eligible_actor_ids is None:
                    result = session.runtime.execute_step(
                        session.current_step,
                        cancellation=cancellation,
                    )
                else:
                    result = session.runtime.execute_step(
                        session.current_step,
                        cancellation=cancellation,
                        eligible_actor_ids=eligible_actor_ids,
                    )
            else:
                execute_human_turn = getattr(
                    session.runtime, "execute_human_turn", None
                )
                if execute_human_turn is None:
                    raise InvalidSessionTransitionError(
                        "simulation runtime does not support interactive turns"
                    )
                result = execute_human_turn(
                    session.current_step,
                    text=human_intent,
                    cancellation=cancellation,
                )
        except SimulationCancelledError:
            with session.lock:
                session.status = TurnSessionStatus.CANCELLED
                session.termination_reason_text = "simulation cancelled"
                session.touch()
                self._executions.release(
                    session.request.project_id,
                    session.request.branch_id,
                    session.session_id,
                )
            raise
        except Exception as error:
            with session.lock:
                if session.status != TurnSessionStatus.CANCELLED:
                    session.status = TurnSessionStatus.FAILED
                    session.termination_reason_text = str(error)
                session.touch()
                self._executions.release(
                    session.request.project_id,
                    session.request.branch_id,
                    session.session_id,
                )
            raise
        with session.lock:
            if cancellation.is_set() or session.status == TurnSessionStatus.CANCELLED:
                if session.status != TurnSessionStatus.CANCELLED:
                    session.status = TurnSessionStatus.CANCELLED
                    session.termination_reason_text = "simulation cancelled"
                    session.touch()
                    self._executions.release(
                        session.request.project_id,
                        session.request.branch_id,
                        session.session_id,
                    )
                raise SimulationCancelledError()
            if result.status == TurnSessionStatus.TERMINATED:
                session.status = TurnSessionStatus.TERMINATED
                session.termination_reason_text = "Game Master ended the session"
            else:
                session.current_step += 1
                session.raw_log_offset += 1
                if result.boundary.value != "none":
                    session.completed_scenes += 1
                reason = hard_limit_reason(
                    session.request.control,
                    completed_steps=session.current_step,
                    completed_scenes=session.completed_scenes,
                    elapsed_seconds=0,
                )
                if reason is not None:
                    session.status = TurnSessionStatus.TERMINATED
                    session.termination_reason_text = reason
            if session.status == TurnSessionStatus.TERMINATED:
                self._executions.release(
                    session.request.project_id,
                    session.request.branch_id,
                    session.session_id,
                )
            session.touch()
            return result.model_copy(update={"status": session.status})

    def restore_to_checkpoint(
        self,
        session_id: str,
        checkpoint: TurnSessionSnapshot,
        *,
        reactivate: bool = False,
    ) -> TurnSessionSnapshot:
        """Replace an in-memory run with its last durable checkpoint state."""
        session = self._get(session_id)
        if checkpoint.session_id != session_id:
            raise ValueError("checkpoint belongs to another session")
        with session.lock:
            reclaim = reactivate and session.status in {
                TurnSessionStatus.TERMINATED,
                TurnSessionStatus.CANCELLED,
                TurnSessionStatus.FAILED,
            }
            if reclaim:
                self._executions.claim(
                    session.request.project_id,
                    session.request.branch_id,
                    session.session_id,
                )
            try:
                restore_snapshot = getattr(session.runtime, "restore_snapshot", None)
                if restore_snapshot is not None:
                    restore_snapshot(checkpoint)
                else:
                    session.runtime.restore_states(
                        actor_states=checkpoint.actor_states,
                        game_master_states=checkpoint.game_master_states,
                        memory_snapshots=checkpoint.memory_snapshots,
                    )
                    session.runtime.set_content_locale(checkpoint.content_locale)
                if reactivate:
                    session.runtime.cancellation.clear()
            except Exception:
                if reclaim:
                    self._executions.release(
                        session.request.project_id,
                        session.request.branch_id,
                        session.session_id,
                    )
                raise
            if reactivate:
                session.status = checkpoint.status
            session.current_step = checkpoint.current_step
            session.completed_scenes = checkpoint.completed_scenes
            session.raw_log_offset = checkpoint.raw_log_offset
            session.total_model_tokens = checkpoint.total_model_tokens
            session.consecutive_model_failures = checkpoint.consecutive_model_failures
            session.checkpoint_id = checkpoint.checkpoint_id
            session.pending_control = PendingControl.NONE
            session.continuous_started_at = None
            if reactivate:
                session.termination_reason_text = checkpoint.termination_reason_text
                session.restoration_notice_text = checkpoint.restoration_notice_text
            session.touch()
            return session.snapshot()

    def advance_one_step(
        self,
        session_id: str,
        *,
        cancellation: Event,
        human_intent: str | None = None,
        eligible_actor_ids: tuple[str, ...] | None = None,
    ) -> StepResult:
        session = self._get(session_id)
        with session.lock:
            self._ensure_can_advance(session)
            if session.status != TurnSessionStatus.RUNNING:
                session.status = TurnSessionStatus.RUNNING
                session.pending_control = PendingControl.NONE
            if session.continuous_started_at is None:
                session.continuous_started_at = time.monotonic()
            session.touch()
        result = self._execute_one(
            session,
            cancellation=cancellation,
            human_intent=human_intent,
            eligible_actor_ids=eligible_actor_ids,
        )
        with session.lock:
            requested_control = session.pending_control
            should_pause = (
                requested_control == PendingControl.PAUSE
                or pauses_after_boundary(
                    session.request.control,
                    result.boundary,
                )
            )
            started = session.continuous_started_at or time.monotonic()
            limit_reason = hard_limit_reason(
                session.request.control,
                completed_steps=session.current_step,
                completed_scenes=session.completed_scenes,
                elapsed_seconds=time.monotonic() - started,
            )
            if limit_reason and session.status == TurnSessionStatus.RUNNING:
                session.status = TurnSessionStatus.TERMINATED
                session.termination_reason_text = limit_reason
            if (
                requested_control == PendingControl.TERMINATE
                and session.status == TurnSessionStatus.RUNNING
            ):
                session.status = TurnSessionStatus.TERMINATED
                self._executions.release(
                    session.request.project_id,
                    session.request.branch_id,
                    session.session_id,
                )
            elif should_pause and session.status == TurnSessionStatus.RUNNING:
                session.status = TurnSessionStatus.PAUSED
            if session.status != TurnSessionStatus.RUNNING:
                session.pending_control = PendingControl.NONE
                session.continuous_started_at = None
            if session.status == TurnSessionStatus.TERMINATED:
                self._executions.release(
                    session.request.project_id,
                    session.request.branch_id,
                    session.session_id,
                )
            session.touch()
            return result.model_copy(update={"status": session.status})

    def begin_continuous(
        self,
        session_id: str,
        *,
        require_paused: bool = False,
    ) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            self._ensure_can_advance(session)
            if require_paused and session.status != TurnSessionStatus.PAUSED:
                raise InvalidSessionTransitionError("only a paused session can resume")
            if session.status == TurnSessionStatus.RUNNING:
                raise InvalidSessionTransitionError("session is already running")
            session.status = TurnSessionStatus.RUNNING
            session.pending_control = PendingControl.NONE
            session.continuous_started_at = time.monotonic()
            session.touch()
            return session.snapshot()

    def pause(self, session_id: str) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            self._ensure_active(session)
            self._ensure_user_override(session)
            session.pending_control = PendingControl.PAUSE
            if session.status != TurnSessionStatus.RUNNING:
                session.status = TurnSessionStatus.PAUSED
            session.touch()
            return session.snapshot()

    def terminate(
        self,
        session_id: str,
        *,
        reason_text: str,
    ) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            self._ensure_active(session)
            self._ensure_user_override(session)
            session.termination_reason_text = reason_text
            if session.status == TurnSessionStatus.RUNNING:
                session.pending_control = PendingControl.TERMINATE
            else:
                session.status = TurnSessionStatus.TERMINATED
                session.pending_control = PendingControl.NONE
                self._executions.release(
                    session.request.project_id,
                    session.request.branch_id,
                    session.session_id,
                )
            session.touch()
            return session.snapshot()

    def cancel(self, session_id: str, *, reason_text: str) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            self._ensure_active(session)
            session.runtime.cancellation.set()
            session.status = TurnSessionStatus.CANCELLED
            session.pending_control = PendingControl.NONE
            session.termination_reason_text = reason_text
            session.touch()
            self._executions.release(
                session.request.project_id,
                session.request.branch_id,
                session.session_id,
            )
            return session.snapshot()

    def restore(self, snapshot: TurnSessionSnapshot) -> TurnSessionSnapshot:
        if calculate_snapshot_state_hash(snapshot) != snapshot.state_hash:
            raise ValueError("simulation snapshot hash mismatch")
        if snapshot.status in {
            TurnSessionStatus.TERMINATED,
            TurnSessionStatus.CANCELLED,
            TurnSessionStatus.FAILED,
        }:
            raise InvalidSessionTransitionError(
                f"cannot restore a {snapshot.status.value} session"
            )
        with self._lock:
            existing = self._sessions.get(snapshot.session_id)
            if existing is not None and existing.status not in {
                TurnSessionStatus.TERMINATED,
                TurnSessionStatus.CANCELLED,
                TurnSessionStatus.FAILED,
            }:
                raise InvalidSessionTransitionError(
                    f"session {snapshot.session_id} is already loaded"
                )
        self._executions.claim(
            snapshot.project_id,
            snapshot.branch_id,
            snapshot.session_id,
        )
        try:
            snapshot_factory = getattr(self._runtime_factory, "from_snapshot", None)
            if snapshot_factory is not None:
                runtime = snapshot_factory(
                    snapshot.session_id,
                    snapshot.request,
                    snapshot,
                )
            else:
                runtime = self._runtime_factory(snapshot.session_id, snapshot.request)
                runtime.restore_states(
                    actor_states=snapshot.actor_states,
                    game_master_states=snapshot.game_master_states,
                    memory_snapshots=snapshot.memory_snapshots,
                )
                runtime.set_content_locale(snapshot.content_locale)
            runtime.cancellation.clear()
            session = SimulationSession(
                session_id=snapshot.session_id,
                request=snapshot.request,
                runtime=runtime,
                status=TurnSessionStatus.PAUSED,
                current_step=snapshot.current_step,
                completed_scenes=snapshot.completed_scenes,
                raw_log_offset=snapshot.raw_log_offset,
                total_model_tokens=snapshot.total_model_tokens,
                consecutive_model_failures=snapshot.consecutive_model_failures,
                checkpoint_id=snapshot.checkpoint_id,
                termination_reason_text=None,
                restoration_notice_text=snapshot.restoration_notice_text,
                started_at=snapshot.started_at,
                updated_at=snapshot.updated_at,
            )
        except Exception:
            self._executions.release(
                snapshot.project_id,
                snapshot.branch_id,
                snapshot.session_id,
            )
            raise
        with self._lock:
            self._sessions[snapshot.session_id] = session
        return session.snapshot()

    def cancel_all(self) -> None:
        with self._lock:
            sessions = tuple(self._sessions.values())
        for session in sessions:
            with session.lock:
                if session.status in {
                    TurnSessionStatus.CREATED,
                    TurnSessionStatus.RUNNING,
                    TurnSessionStatus.PAUSED,
                }:
                    session.runtime.cancellation.set()
                    session.status = TurnSessionStatus.CANCELLED
                    session.termination_reason_text = "application shutdown"
                    session.touch()
                    self._executions.release(
                        session.request.project_id,
                        session.request.branch_id,
                        session.session_id,
                    )
