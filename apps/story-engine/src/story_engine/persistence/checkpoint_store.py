from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from story_engine.domain.simulation import TurnSessionSnapshot
from story_engine.simulation.session import calculate_snapshot_state_hash
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope


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
        return self.directory / f"{checkpoint_id}.md"

    def prepare(self, snapshot: TurnSessionSnapshot) -> tuple[str, Path, str]:
        calculated = calculate_snapshot_state_hash(snapshot)
        if calculated != snapshot.state_hash:
            raise ValueError("snapshot state hash mismatch")
        checkpoint_id = f"checkpoint-{snapshot.state_hash}"
        persisted = snapshot.model_copy(update={"checkpoint_id": checkpoint_id})
        envelope = CheckpointEnvelope(
            checkpoint_id=checkpoint_id,
            snapshot=persisted,
        )
        content = dump_json_envelope(
            schema="story-engine/checkpoint/v1",
            title=f"Checkpoint {checkpoint_id}",
            metadata={
                "checkpoint_id": checkpoint_id,
                "branch_id": snapshot.branch_id,
                "step": snapshot.current_step,
            },
            payload=envelope.model_dump(mode="json"),
        )
        return checkpoint_id, self.path_for(checkpoint_id), content

    def save(self, snapshot: TurnSessionSnapshot) -> tuple[str, Path]:
        checkpoint_id, path, content = self.prepare(snapshot)
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != content:
                raise ValueError("checkpoint content hash collision")
            return checkpoint_id, path
        atomic_write_text(path, content, overwrite=False)
        return checkpoint_id, path

    def load(self, checkpoint_id: str) -> TurnSessionSnapshot:
        path = self.path_for(checkpoint_id)
        try:
            payload = load_json_envelope(
                path,
                schema="story-engine/checkpoint/v1",
            )
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
