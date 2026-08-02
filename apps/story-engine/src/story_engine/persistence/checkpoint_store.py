import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from story_engine.domain.simulation import TurnSessionSnapshot
from story_engine.simulation.session import calculate_snapshot_state_hash
from story_engine.workspace.atomic import atomic_write_text


class CheckpointEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    checkpoint_id: str = Field(pattern=r"^checkpoint-[0-9a-f]{64}$")
    snapshot: TurnSessionSnapshot


class CheckpointStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / ".story-engine/runtime/checkpoints"

    def path_for(self, checkpoint_id: str) -> Path:
        if not checkpoint_id.startswith("checkpoint-") or len(checkpoint_id) != 75:
            raise ValueError("invalid checkpoint ID")
        return self.directory / f"{checkpoint_id}.json"

    def save(self, snapshot: TurnSessionSnapshot) -> tuple[str, Path]:
        calculated = calculate_snapshot_state_hash(snapshot)
        if calculated != snapshot.state_hash:
            raise ValueError("snapshot state hash mismatch")
        checkpoint_id = f"checkpoint-{snapshot.state_hash}"
        persisted = snapshot.model_copy(update={"checkpoint_id": checkpoint_id})
        envelope = CheckpointEnvelope(
            checkpoint_id=checkpoint_id,
            snapshot=persisted,
        )
        content = json.dumps(
            envelope.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        path = self.path_for(checkpoint_id)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != f"{content}\n":
                raise ValueError("checkpoint content hash collision")
            return checkpoint_id, path
        atomic_write_text(path, f"{content}\n", overwrite=False)
        return checkpoint_id, path

    def load(self, checkpoint_id: str) -> TurnSessionSnapshot:
        path = self.path_for(checkpoint_id)
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
            envelope = CheckpointEnvelope.model_validate(payload)
        except (OSError, ValueError) as error:
            raise ValueError(f"checkpoint {checkpoint_id!r} is invalid") from error
        if envelope.checkpoint_id != checkpoint_id:
            raise ValueError("checkpoint envelope ID mismatch")
        snapshot = envelope.snapshot
        if f"checkpoint-{snapshot.state_hash}" != checkpoint_id:
            raise ValueError("checkpoint ID does not match state hash")
        if calculate_snapshot_state_hash(snapshot) != snapshot.state_hash:
            raise ValueError("checkpoint state hash verification failed")
        return snapshot

    def exists(self, checkpoint_id: str) -> bool:
        try:
            return self.path_for(checkpoint_id).is_file()
        except ValueError:
            return False
