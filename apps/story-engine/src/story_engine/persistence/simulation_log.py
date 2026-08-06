import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, Field

from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.simulation import StepResult, TurnSessionSnapshot
from story_engine.domain.trace import TurnTrace
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope
from story_engine.workspace.lock import ProjectLock

if TYPE_CHECKING:
    from story_engine.persistence.checkpoint_store import CheckpointStore

_BRANCH_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,127}$")


class SimulationLogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=2, ge=2)
    parent_checkpoint_id: str | None = Field(
        default=None,
        pattern=r"^checkpoint-[0-9a-f]{64}$",
    )
    checkpoint_id: str | None = Field(
        default=None,
        pattern=r"^checkpoint-[0-9a-f]{64}$",
    )
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: StepResult
    trace: TurnTrace


class SimulationLogStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / "history/turns"
        self.observation_directory = root / "history/observations"

    def path_for(self, branch_id: str) -> Path:
        if not _BRANCH_ID.fullmatch(branch_id):
            raise ValueError("invalid branch ID")
        return self.directory / branch_id

    def path_for_record(self, record: SimulationLogRecord) -> Path:
        trace_id = quote(record.trace.trace_id, safe="")
        return self.path_for(record.result.branch_id) / (
            f"{record.result.step:08d}-{trace_id}.md"
        )

    @staticmethod
    def _content(record: SimulationLogRecord) -> str:
        action = record.result.action_text or "No actor action was produced."
        return dump_json_envelope(
            schema="story-engine/raw-turn/v1",
            title=f"Turn {record.result.step}",
            metadata={
                "branch_id": record.result.branch_id,
                "session_id": record.result.session_id,
                "step": record.result.step,
                "trace_id": record.trace.trace_id,
                "checkpoint_id": record.checkpoint_id,
                "parent_checkpoint_id": record.parent_checkpoint_id,
            },
            body=f"# Turn {record.result.step}\n\n## Action\n\n{action}",
            payload=record.model_dump(mode="json"),
        )

    def prepare(self, record: SimulationLogRecord) -> tuple[Path, str]:
        return self.path_for_record(record), self._content(record)

    def append(self, branch_id: str, record: SimulationLogRecord) -> Path:
        if branch_id != record.result.branch_id:
            raise ValueError("simulation log branch mismatch")
        path, content = self.prepare(record)
        with ProjectLock(self.root):
            existing = self.read(branch_id)
            duplicate = next(
                (
                    item
                    for item in existing
                    if item.trace.trace_id == record.trace.trace_id
                ),
                None,
            )
            if duplicate is not None:
                if duplicate != record:
                    raise ValueError("conflicting duplicate simulation log step")
                return path
            atomic_write_text(path, content, overwrite=False)
        return path

    def read(self, branch_id: str) -> tuple[SimulationLogRecord, ...]:
        directory = self.path_for(branch_id)
        if not directory.exists():
            return ()
        records = []
        try:
            for path in directory.glob("*.md"):
                payload = load_json_envelope(
                    path,
                    schema="story-engine/raw-turn/v1",
                )
                records.append(SimulationLogRecord.model_validate(payload))
        except (OSError, ValueError) as error:
            raise ValueError(f"simulation log {branch_id!r} is invalid") from error
        records.sort(
            key=lambda record: (
                record.result.step,
                record.trace.started_at,
                record.trace.trace_id,
            )
        )
        return tuple(records)

    def read_all(self) -> tuple[SimulationLogRecord, ...]:
        if not self.directory.exists():
            return ()
        return tuple(
            record
            for branch_dir in sorted(self.directory.iterdir())
            if branch_dir.is_dir()
            for record in self.read(branch_dir.name)
        )

    def reachable(
        self,
        checkpoints: "CheckpointStore",
        checkpoint_id: str,
    ) -> tuple[SimulationLogRecord, ...]:
        lineage = checkpoints.lineage(checkpoint_id)
        by_checkpoint: dict[str, SimulationLogRecord] = {}
        for record in self.read_all():
            if record.checkpoint_id not in lineage:
                continue
            assert record.checkpoint_id is not None
            if record.checkpoint_id in by_checkpoint:
                raise ValueError("checkpoint has multiple simulation turn records")
            by_checkpoint[record.checkpoint_id] = record
        return tuple(
            by_checkpoint[item] for item in lineage if item in by_checkpoint
        )

    def prepare_observations(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        step: int,
    ) -> tuple[tuple[Path, str], ...]:
        prepared: list[tuple[Path, str]] = []
        for memory_snapshot in snapshot.memory_snapshots.values():
            bank = ConcordiaMemoryBank(
                owner_id=memory_snapshot.owner_id,
                scope=memory_snapshot.scope,
            )
            bank.restore(memory_snapshot)
            for record in bank.scan(
                lambda item: (
                    item.step == step
                    and item.scope == MemoryScope.CHARACTER
                    and item.record_type == MemoryRecordType.OBSERVATION
                )
            ):
                prepared.append(self._prepare_observation(record))
        prepared.sort(key=lambda item: item[0].as_posix())
        return tuple(prepared)

    def _prepare_observation(self, record: MemoryRecord) -> tuple[Path, str]:
        filename = f"{quote(record.record_id, safe='')}.md"
        path = (
            self.observation_directory
            / record.branch_id
            / quote(record.owner_id, safe="")
            / filename
        )
        content = dump_json_envelope(
            schema="story-engine/raw-observation/v1",
            title=f"Observation {record.record_id}",
            metadata={
                "source_id": record.record_id,
                "branch_id": record.branch_id,
                "subject_id": record.owner_id,
                "step": record.step,
                "permission": "private",
            },
            body=f"# Observation\n\n{record.text}",
            payload=record.model_dump(mode="json"),
        )
        return path, content

    def read_observations(
        self,
        branch_id: str,
        *,
        subject_id: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        directory = self.observation_directory / branch_id
        if subject_id is not None:
            directory /= quote(subject_id, safe="")
        if not directory.exists():
            return ()
        records = tuple(
            MemoryRecord.model_validate(
                load_json_envelope(
                    path,
                    schema="story-engine/raw-observation/v1",
                )
            )
            for path in directory.rglob("*.md")
        )
        return tuple(
            sorted(
                records,
                key=lambda item: (item.step, item.created_at, item.record_id),
            )
        )

    def find_source(self, branch_id: str, source_id: str) -> Path:
        encoded = f"{quote(source_id, safe='')}.md"
        observations = tuple(
            (self.observation_directory / branch_id).rglob(encoded)
        )
        if len(observations) == 1:
            return observations[0]
        for path in self.path_for(branch_id).glob("*.md"):
            record = SimulationLogRecord.model_validate(
                load_json_envelope(path, schema="story-engine/raw-turn/v1")
            )
            source_ids = {
                record.trace.trace_id,
                record.trace.putative_event_record_id,
                *record.trace.resolved_event_record_ids,
            }
            if record.result.action_text:
                source_ids.add(
                    f"action:{record.result.session_id}:{record.result.step}"
                )
            if record.result.resolved_turn is not None:
                source_ids.update(
                    event.event_id for event in record.result.resolved_turn.events
                )
            if source_id in source_ids:
                return path
        raise FileNotFoundError(source_id)
