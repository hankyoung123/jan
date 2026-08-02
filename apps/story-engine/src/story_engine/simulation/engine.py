import time
import uuid
from collections.abc import Callable
from threading import Event, RLock

from story_engine.concordia_runtime.resolver import SimulationCancelledError
from story_engine.domain.simulation import (
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import ModelCallTrace
from story_engine.simulation.control import hard_limit_reason, pauses_after_step
from story_engine.simulation.execution import SessionExecutionRegistry
from story_engine.simulation.runtime import StorySimulationRuntime
from story_engine.simulation.session import SimulationSession

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

    def _execute_one(
        self,
        session: SimulationSession,
        *,
        cancellation: Event,
    ) -> StepResult:
        if cancellation.is_set():
            session.runtime.cancellation.set()
        try:
            result = session.runtime.execute_step(
                session.current_step,
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
            if result.status == TurnSessionStatus.TERMINATED:
                session.status = TurnSessionStatus.TERMINATED
                session.termination_reason_text = "Game Master ended the session"
            else:
                session.current_step += 1
                session.raw_log_offset += 1
                reason = hard_limit_reason(
                    session.request.control,
                    completed_steps=session.current_step,
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

    def step(self, session_id: str, *, cancellation: Event) -> StepResult:
        session = self._get(session_id)
        with session.lock:
            self._ensure_active(session)
            session.status = TurnSessionStatus.RUNNING
            session.pause_requested = False
            session.touch()
        result = self._execute_one(session, cancellation=cancellation)
        with session.lock:
            if session.status == TurnSessionStatus.RUNNING:
                session.status = TurnSessionStatus.PAUSED
                session.touch()
                result = result.model_copy(update={"status": session.status})
        return result

    def run(
        self,
        session_id: str,
        *,
        cancellation: Event,
        on_step: Callable[[StepResult], None] | None = None,
    ) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            self._ensure_active(session)
            session.status = TurnSessionStatus.RUNNING
            session.pause_requested = False
            session.touch()
            started = time.monotonic()
        while True:
            result = self._execute_one(session, cancellation=cancellation)
            with session.lock:
                should_pause = session.pause_requested or pauses_after_step(
                    session.request.control
                )
                limit_reason = hard_limit_reason(
                    session.request.control,
                    completed_steps=session.current_step,
                    elapsed_seconds=time.monotonic() - started,
                )
                if limit_reason and session.status == TurnSessionStatus.RUNNING:
                    session.status = TurnSessionStatus.TERMINATED
                    session.termination_reason_text = limit_reason
                if should_pause and session.status == TurnSessionStatus.RUNNING:
                    session.status = TurnSessionStatus.PAUSED
                session.touch()
                result_for_sink = result.model_copy(update={"status": session.status})
            if on_step is not None:
                on_step(result_for_sink)
            with session.lock:
                if session.status != TurnSessionStatus.RUNNING:
                    session.touch()
                    return session.snapshot()

    def pause(self, session_id: str) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            self._ensure_active(session)
            session.pause_requested = True
            if session.status != TurnSessionStatus.RUNNING:
                session.status = TurnSessionStatus.PAUSED
            session.touch()
            return session.snapshot()

    def resume(
        self,
        session_id: str,
        *,
        cancellation: Event,
        on_step: Callable[[StepResult], None] | None = None,
    ) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            if session.status != TurnSessionStatus.PAUSED:
                raise InvalidSessionTransitionError("only a paused session can resume")
        return self.run(session_id, cancellation=cancellation, on_step=on_step)

    def terminate(
        self,
        session_id: str,
        *,
        reason_text: str,
    ) -> TurnSessionSnapshot:
        session = self._get(session_id)
        with session.lock:
            self._ensure_active(session)
            session.runtime.cancellation.set()
            session.status = TurnSessionStatus.TERMINATED
            session.termination_reason_text = reason_text
            session.touch()
            self._executions.release(
                session.request.project_id,
                session.request.branch_id,
                session.session_id,
            )
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
