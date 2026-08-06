import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, RLock
from typing import Any, TypeVar, cast

from story_engine.domain.simulation import StepResult, TurnSessionSnapshot
from story_engine.persistence.command_store import (
    CommandReceiptCommit,
    CommandReceiptStore,
)

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

    def __init__(
        self,
        receipt_store_factory: Callable[[str], CommandReceiptStore] | None = None,
    ) -> None:
        self._session_locks: dict[str, Lock] = {}
        self._receipts: dict[tuple[str, str], CommandReceipt] = {}
        self._receipt_store_factory = receipt_store_factory
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
        record_receipt: bool = True,
    ) -> CommandResult:
        with self._session_lock(session_id):
            key = (session_id, command_id)
            state = current_state()
            persisted = self._load_persisted(
                state,
                session_id=session_id,
                command_id=command_id,
            )
            receipt: CommandReceipt | None
            if persisted is not None:
                receipt = self._decode_receipt(persisted)
            else:
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

            actual_state_hash = state.state_hash
            if actual_state_hash != expected_state_hash:
                raise SessionCommandConflictError(
                    "simulation state changed; refresh before issuing another command"
                )

            result = command()
            if record_receipt:
                self.record(
                    session_id=session_id,
                    command_id=command_id,
                    operation=operation,
                    expected_state_hash=expected_state_hash,
                    result=result,
                    project_id=state.project_id,
                )
            return result

    def receipt_commit(
        self,
        *,
        command_id: str,
        operation: str,
        expected_state_hash: str,
    ) -> CommandReceiptCommit:
        return CommandReceiptCommit(
            command_id=command_id,
            operation=operation,
            expected_state_hash=expected_state_hash,
            request_fingerprint=self._fingerprint(operation, expected_state_hash),
        )

    def record(
        self,
        *,
        session_id: str,
        command_id: str,
        operation: str,
        expected_state_hash: str,
        result: CommandResult,
        project_id: str,
    ) -> None:
        receipt = CommandReceipt(
            operation=operation,
            expected_state_hash=expected_state_hash,
            result=result,
        )
        key = (session_id, command_id)
        if self._receipt_store_factory is None:
            with self._lock:
                existing = self._receipts.get(key)
                if existing is not None and existing != receipt:
                    raise SessionCommandConflictError(
                        f"command {command_id!r} was already used with different input"
                    )
                self._receipts[key] = receipt
            return
        model_dump = getattr(result, "model_dump", None)
        if model_dump is None:
            raise TypeError("command results must be Pydantic models")
        self._receipt_store_factory(project_id).save(
            session_id=session_id,
            command_id=command_id,
            operation=operation,
            expected_state_hash=expected_state_hash,
            request_fingerprint=self._fingerprint(operation, expected_state_hash),
            result_type=type(result).__name__,
            result=model_dump(mode="json"),
            committed_checkpoint_id=getattr(result, "checkpoint_id", None),
        )

    def _load_persisted(
        self,
        state: TurnSessionSnapshot,
        *,
        session_id: str,
        command_id: str,
    ) -> dict[str, Any] | None:
        if self._receipt_store_factory is None:
            return None
        return self._receipt_store_factory(state.project_id).load(
            session_id=session_id,
            command_id=command_id,
        )

    @staticmethod
    def _fingerprint(operation: str, expected_state_hash: str) -> str:
        return hashlib.sha256(
            f"{operation}:{expected_state_hash}".encode()
        ).hexdigest()

    @staticmethod
    def _decode_receipt(payload: dict[str, Any]) -> CommandReceipt:
        result_type = payload.get("result_type")
        result_payload = payload.get("result")
        if not isinstance(result_payload, dict):
            raise ValueError("command receipt result is invalid")
        result_model = (
            StepResult if result_type == "StepResult" else TurnSessionSnapshot
        )
        return CommandReceipt(
            operation=str(payload["operation"]),
            expected_state_hash=str(payload["expected_state_hash"]),
            result=result_model.model_validate(result_payload),
        )
