"""Durable command receipts for restart-safe idempotency."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope
from story_engine.workspace.lock import ProjectLock

_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


@dataclass(frozen=True, slots=True)
class CommandReceiptCommit:
    """Command metadata carried into the core step transaction."""

    command_id: str
    operation: str
    expected_state_hash: str
    request_fingerprint: str


class CommandReceiptStore:
    """Persist one command result below its owning session."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / ".story-engine/runtime/sessions"

    def _path(self, session_id: str, command_id: str) -> Path:
        if not _ID.fullmatch(session_id) or not _ID.fullmatch(command_id):
            raise ValueError("invalid session or command ID")
        return (
            self.directory
            / session_id.replace(":", "__")
            / "commands"
            / f"{command_id.replace(':', '__')}.json"
        )

    def _build(
        self,
        *,
        session_id: str,
        command_id: str,
        operation: str,
        expected_state_hash: str,
        request_fingerprint: str,
        result_type: str,
        result: dict[str, Any],
        committed_checkpoint_id: str | None,
    ) -> tuple[Path, str, dict[str, Any]]:
        path = self._path(session_id, command_id)
        payload = {
            "session_id": session_id,
            "command_id": command_id,
            "operation": operation,
            "expected_state_hash": expected_state_hash,
            "request_fingerprint": request_fingerprint,
            "result_type": result_type,
            "result": result,
            "committed_checkpoint_id": committed_checkpoint_id,
            "created_at": datetime.now(UTC).isoformat(),
        }
        content = dump_json_envelope(
            schema="story-engine/command-receipt/v1",
            title=f"Simulation Command Receipt {command_id}",
            metadata={
                "session_id": session_id,
                "command_id": command_id,
                "operation": operation,
            },
            payload=payload,
        )
        return path, content, payload

    def prepare(
        self,
        *,
        session_id: str,
        command_id: str,
        operation: str,
        expected_state_hash: str,
        request_fingerprint: str,
        result_type: str,
        result: dict[str, Any],
        committed_checkpoint_id: str | None,
    ) -> tuple[Path, str]:
        path, content, _ = self._build(
            session_id=session_id,
            command_id=command_id,
            operation=operation,
            expected_state_hash=expected_state_hash,
            request_fingerprint=request_fingerprint,
            result_type=result_type,
            result=result,
            committed_checkpoint_id=committed_checkpoint_id,
        )
        return path, content

    def save(
        self,
        *,
        session_id: str,
        command_id: str,
        operation: str,
        expected_state_hash: str,
        request_fingerprint: str,
        result_type: str,
        result: dict[str, Any],
        committed_checkpoint_id: str | None,
    ) -> None:
        path, content, payload = self._build(
            session_id=session_id,
            command_id=command_id,
            operation=operation,
            expected_state_hash=expected_state_hash,
            request_fingerprint=request_fingerprint,
            result_type=result_type,
            result=result,
            committed_checkpoint_id=committed_checkpoint_id,
        )
        with ProjectLock(self.root):
            if path.exists():
                existing = load_json_envelope(
                    path,
                    schema="story-engine/command-receipt/v1",
                )
                comparable_existing = dict(existing)
                comparable_existing.pop("created_at", None)
                comparable_payload = dict(payload)
                comparable_payload.pop("created_at", None)
                if comparable_existing != comparable_payload:
                    raise ValueError("command receipt collision")
                return
            atomic_write_text(path, content, overwrite=False)

    def load(self, *, session_id: str, command_id: str) -> dict[str, Any] | None:
        path = self._path(session_id, command_id)
        try:
            return dict(
                load_json_envelope(
                    path,
                    schema="story-engine/command-receipt/v1",
                )
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            raise ValueError(f"command receipt {command_id!r} is invalid") from error
