from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, RLock
from typing import TypeVar, cast

from story_engine.domain.simulation import TurnSessionSnapshot

CommandResult = TypeVar("CommandResult")


class SessionCommandConflictError(RuntimeError):
    """Raised when a command cannot run against the current session state."""


@dataclass(frozen=True, slots=True)
class CommandReceipt:
    operation: str
    expected_state_hash: str
    result: object


class SessionCommandCoordinator:
    """Serialize advancing commands and retain their idempotent results."""

    def __init__(self) -> None:
        self._session_locks: dict[str, Lock] = {}
        self._receipts: dict[tuple[str, str], CommandReceipt] = {}
        self._lock = RLock()

    def _session_lock(self, session_id: str) -> Lock:
        with self._lock:
            return self._session_locks.setdefault(session_id, Lock())

    def execute(
        self,
        *,
        session_id: str,
        command_id: str,
        operation: str,
        expected_state_hash: str,
        current_state: Callable[[], TurnSessionSnapshot],
        command: Callable[[], CommandResult],
    ) -> CommandResult:
        with self._session_lock(session_id):
            key = (session_id, command_id)
            with self._lock:
                receipt = self._receipts.get(key)
            if receipt is not None:
                if (
                    receipt.operation != operation
                    or receipt.expected_state_hash != expected_state_hash
                ):
                    raise SessionCommandConflictError(
                        f"command {command_id!r} was already used with different input"
                    )
                return cast(CommandResult, receipt.result)

            actual_state_hash = current_state().state_hash
            if actual_state_hash != expected_state_hash:
                raise SessionCommandConflictError(
                    "simulation state changed; refresh before issuing another command"
                )

            result = command()
            with self._lock:
                self._receipts[key] = CommandReceipt(
                    operation=operation,
                    expected_state_hash=expected_state_hash,
                    result=result,
                )
            return result
