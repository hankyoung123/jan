import hashlib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from story_engine.domain.simulation import TurnSessionSnapshot
from story_engine.simulation.session import calculate_snapshot_state_hash
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope


class CheckpointEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=3, ge=3)
    checkpoint_id: str = Field(pattern=r"^checkpoint-[0-9a-f]{64}$")
    parent_checkpoint_id: str | None = Field(
        default=None,
        pattern=r"^checkpoint-[0-9a-f]{64}$",
    )
    snapshot: TurnSessionSnapshot


class CheckpointStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / ".story-engine/runtime/checkpoints"

    def path_for(self, checkpoint_id: str) -> Path:
        if not checkpoint_id.startswith("checkpoint-") or len(checkpoint_id) != 75:
            raise ValueError("invalid checkpoint ID")
        return self.directory / f"{checkpoint_id}.md"

    @staticmethod
    def _checkpoint_id(state_hash: str, parent_checkpoint_id: str | None) -> str:
        lineage_key = f"{parent_checkpoint_id or ''}\0{state_hash}"
        digest = hashlib.sha256(lineage_key.encode("utf-8")).hexdigest()
        return f"checkpoint-{digest}"

    def prepare(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        parent_checkpoint_id: str | None = None,
    ) -> tuple[str, Path, str]:
        calculated = calculate_snapshot_state_hash(snapshot)
        if calculated != snapshot.state_hash:
            raise ValueError("snapshot state hash mismatch")
        if parent_checkpoint_id is not None and not self.exists(parent_checkpoint_id):
            raise ValueError("parent checkpoint does not exist")
        checkpoint_id = self._checkpoint_id(snapshot.state_hash, parent_checkpoint_id)
        persisted = snapshot.model_copy(update={"checkpoint_id": checkpoint_id})
        envelope = CheckpointEnvelope(
            checkpoint_id=checkpoint_id,
            parent_checkpoint_id=parent_checkpoint_id,
            snapshot=persisted,
        )
        content = dump_json_envelope(
            schema="story-engine/checkpoint/v1",
            title=f"Checkpoint {checkpoint_id}",
            metadata={
                "checkpoint_id": checkpoint_id,
                "branch_id": snapshot.branch_id,
                "step": snapshot.current_step,
                "parent_checkpoint_id": parent_checkpoint_id,
            },
            payload=envelope.model_dump(mode="json"),
        )
        return checkpoint_id, self.path_for(checkpoint_id), content

    def save(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        parent_checkpoint_id: str | None = None,
    ) -> tuple[str, Path]:
        checkpoint_id, path, content = self.prepare(
            snapshot,
            parent_checkpoint_id=parent_checkpoint_id,
        )
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            if existing != content:
                raise ValueError("checkpoint content hash collision")
            return checkpoint_id, path
        atomic_write_text(path, content, overwrite=False)
        return checkpoint_id, path

    def load_envelope(self, checkpoint_id: str) -> CheckpointEnvelope:
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
        expected = self._checkpoint_id(
            snapshot.state_hash,
            envelope.parent_checkpoint_id,
        )
        if expected != checkpoint_id:
            raise ValueError("checkpoint ID does not match state hash")
        if calculate_snapshot_state_hash(snapshot) != snapshot.state_hash:
            raise ValueError("checkpoint state hash verification failed")
        return envelope

    def load(self, checkpoint_id: str) -> TurnSessionSnapshot:
        return self.load_envelope(checkpoint_id).snapshot

    def parent_id(self, checkpoint_id: str) -> str | None:
        return self.load_envelope(checkpoint_id).parent_checkpoint_id

    def lineage(self, checkpoint_id: str) -> tuple[str, ...]:
        """Return immutable Checkpoint ancestry in root-to-target order."""
        reverse: list[str] = []
        seen: set[str] = set()
        current: str | None = checkpoint_id
        while current is not None:
            if current in seen:
                raise ValueError("checkpoint lineage contains a cycle")
            seen.add(current)
            reverse.append(current)
            current = self.parent_id(current)
        return tuple(reversed(reverse))

    def exists(self, checkpoint_id: str) -> bool:
        try:
            return self.path_for(checkpoint_id).is_file()
        except ValueError:
            return False
