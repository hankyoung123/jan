from dataclasses import dataclass
from threading import Event, RLock

from pydantic import Field

from story_engine.domain.errors import DomainError
from story_engine.domain.models import DomainModel


class TurnAlreadyRunningError(DomainError):
    """Raised when a project already owns an active turn execution."""


class TurnNotRunningError(DomainError):
    """Raised when cancellation targets a project with no active execution."""


class TurnCancelledError(DomainError):
    """Raised cooperatively after an active turn receives cancellation."""


class TurnCancellationResult(DomainModel):
    project_id: str = Field(min_length=1)
    turn_id: str | None = None
    cancel_requested: bool = True


@dataclass(slots=True)
class TurnExecution:
    cancellation: Event
    turn_id: str | None = None


class TurnExecutionRegistry:
    """Own process-local cancellation state for one active turn per project."""

    def __init__(self) -> None:
        self._executions: dict[str, TurnExecution] = {}
        self._lock = RLock()

    def begin(self, project_id: str) -> TurnExecution:
        with self._lock:
            if project_id in self._executions:
                raise TurnAlreadyRunningError(
                    f"project {project_id!r} already has a running turn"
                )
            execution = TurnExecution(cancellation=Event())
            self._executions[project_id] = execution
            return execution

    def identify(
        self,
        project_id: str,
        execution: TurnExecution,
        turn_id: str,
    ) -> None:
        with self._lock:
            if self._executions.get(project_id) is execution:
                execution.turn_id = turn_id

    def cancel(self, project_id: str) -> TurnCancellationResult:
        with self._lock:
            execution = self._executions.get(project_id)
            if execution is None:
                raise TurnNotRunningError(
                    f"project {project_id!r} has no running turn"
                )
            execution.cancellation.set()
            return TurnCancellationResult(
                project_id=project_id,
                turn_id=execution.turn_id,
            )

    def claim_completion(self, project_id: str, execution: TurnExecution) -> bool:
        """Atomically choose completed output over a concurrent cancellation."""

        with self._lock:
            if self._executions.get(project_id) is not execution:
                return False
            self._executions.pop(project_id)
            return not execution.cancellation.is_set()

    def finish(self, project_id: str, execution: TurnExecution) -> None:
        with self._lock:
            if self._executions.get(project_id) is execution:
                self._executions.pop(project_id)

    def cancel_all(self) -> None:
        with self._lock:
            executions = tuple(self._executions.values())
        for execution in executions:
            execution.cancellation.set()
