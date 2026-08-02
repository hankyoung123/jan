import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import RLock
from typing import Any

from story_engine.domain.simulation import (
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.simulation.runtime import StorySimulationRuntime


def _state_hash(payload: dict[str, Any]) -> str:
    content = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def calculate_snapshot_state_hash(snapshot: TurnSessionSnapshot) -> str:
    payload = snapshot.model_dump(mode="json")
    payload.pop("state_hash")
    payload.pop("checkpoint_id")
    return _state_hash(payload)


@dataclass(slots=True)
class SimulationSession:
    session_id: str
    request: TurnSessionRequest
    runtime: StorySimulationRuntime
    status: TurnSessionStatus = TurnSessionStatus.CREATED
    current_step: int = 0
    raw_log_offset: int = 0
    total_model_tokens: int = 0
    consecutive_model_failures: int = 0
    checkpoint_id: str | None = None
    termination_reason_text: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    pause_requested: bool = False
    lock: RLock = field(default_factory=RLock)

    def touch(self) -> None:
        self.updated_at = datetime.now(UTC)

    def snapshot(self) -> TurnSessionSnapshot:
        actor_states = self.runtime.actor_states()
        game_master_states = self.runtime.game_master_states()
        memory_snapshots = self.runtime.memory_snapshots()
        provisional = TurnSessionSnapshot(
            session_id=self.session_id,
            project_id=self.request.project_id,
            branch_id=self.request.branch_id,
            status=self.status,
            content_locale=self.request.content_locale,
            current_step=self.current_step,
            actor_states=actor_states,
            game_master_states=game_master_states,
            memory_snapshots=memory_snapshots,
            raw_log_offset=self.raw_log_offset,
            total_model_tokens=self.total_model_tokens,
            consecutive_model_failures=self.consecutive_model_failures,
            checkpoint_id=self.checkpoint_id,
            started_at=self.started_at,
            updated_at=self.updated_at,
            termination_reason_text=self.termination_reason_text,
            state_hash="0" * 64,
        )
        return provisional.model_copy(
            update={"state_hash": calculate_snapshot_state_hash(provisional)}
        )
