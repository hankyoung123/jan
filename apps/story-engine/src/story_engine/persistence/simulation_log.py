import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from story_engine.domain.memory import MemoryRecord
from story_engine.domain.simulation import StepResult
from story_engine.domain.trace import TurnTrace
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope
from story_engine.workspace.lock import ProjectLock

if TYPE_CHECKING:
    from story_engine.persistence.checkpoint_store import CheckpointStore

_BRANCH_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,127}$")


class SimulationLogRecord(BaseModel):
    """One immutable node in the durable cognitive-history chain."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=4, ge=4)
    record_kind: Literal["genesis", "turn"] = "turn"
    log_id: str = Field(pattern=r"^log-[0-9a-f]{64}$")
    parent_log_id: str | None = Field(
        default=None,
        pattern=r"^log-[0-9a-f]{64}$",
    )
    checkpoint_id: str | None = Field(
        default=None,
        pattern=r"^checkpoint-[0-9a-f]{64}$",
    )
    result: StepResult
    trace: TurnTrace
    memory_delta: tuple[MemoryRecord, ...] = ()

    @staticmethod
    def _hash_payload(
        *,
        record_kind: str,
        parent_log_id: str | None,
        result: StepResult,
        trace: TurnTrace,
        memory_delta: tuple[MemoryRecord, ...],
    ) -> dict[str, object]:
        return {
            "schema_version": 4,
            "record_kind": record_kind,
            "parent_log_id": parent_log_id,
            "result": result.model_dump(mode="json", exclude={"checkpoint_id"}),
            "trace": trace.model_dump(mode="json"),
            "memory_delta": [
                record.model_dump(mode="json") for record in memory_delta
            ],
        }

    @classmethod
    def calculate_log_id(
        cls,
        *,
        record_kind: str,
        parent_log_id: str | None,
        result: StepResult,
        trace: TurnTrace,
        memory_delta: tuple[MemoryRecord, ...],
    ) -> str:
        content = json.dumps(
            cls._hash_payload(
                record_kind=record_kind,
                parent_log_id=parent_log_id,
                result=result,
                trace=trace,
                memory_delta=memory_delta,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return f"log-{hashlib.sha256(content.encode()).hexdigest()}"

    @classmethod
    def create(
        cls,
        *,
        record_kind: Literal["genesis", "turn"],
        parent_log_id: str | None,
        result: StepResult,
        trace: TurnTrace,
        memory_delta: tuple[MemoryRecord, ...],
        checkpoint_id: str | None = None,
    ) -> "SimulationLogRecord":
        durable_delta = tuple(
            record.model_copy(update={"raw_text": None}) for record in memory_delta
        )
        log_id = cls.calculate_log_id(
            record_kind=record_kind,
            parent_log_id=parent_log_id,
            result=result,
            trace=trace,
            memory_delta=durable_delta,
        )
        persisted_result = result.model_copy(update={"checkpoint_id": checkpoint_id})
        return cls(
            record_kind=record_kind,
            log_id=log_id,
            parent_log_id=parent_log_id,
            checkpoint_id=checkpoint_id,
            result=persisted_result,
            trace=trace,
            memory_delta=durable_delta,
        )

    def with_checkpoint(self, checkpoint_id: str) -> "SimulationLogRecord":
        return self.model_copy(
            update={
                "checkpoint_id": checkpoint_id,
                "result": self.result.model_copy(
                    update={"checkpoint_id": checkpoint_id}
                ),
            }
        )

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = self.calculate_log_id(
            record_kind=self.record_kind,
            parent_log_id=self.parent_log_id,
            result=self.result,
            trace=self.trace,
            memory_delta=self.memory_delta,
        )
        if self.log_id != expected:
            raise ValueError("simulation log hash mismatch")
        if (
            self.result.branch_id != self.trace.branch_id
            or self.result.session_id != self.trace.session_id
            or self.result.step != self.trace.step
        ):
            raise ValueError("simulation log result and trace identity mismatch")
        if self.record_kind == "genesis" and self.parent_log_id is not None:
            raise ValueError("genesis log cannot have a parent")
        if any(
            record.branch_id != self.result.branch_id
            for record in self.memory_delta
        ):
            raise ValueError("memory delta branch does not match simulation log")
        return self


class SimulationLogStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / "history/turns"

    def path_for(self, branch_id: str) -> Path:
        if not _BRANCH_ID.fullmatch(branch_id):
            raise ValueError("invalid branch ID")
        return self.directory / branch_id

    def path_for_record(self, record: SimulationLogRecord) -> Path:
        return self.path_for(record.result.branch_id) / (
            f"{record.result.step:08d}-{record.record_kind}-{record.log_id}.md"
        )

    @staticmethod
    def _content(record: SimulationLogRecord) -> str:
        if record.record_kind == "genesis":
            title = "Genesis Memory"
            body = "# Genesis Memory\n\nInitial durable cognitive history."
        else:
            title = f"Turn {record.result.step}"
            action = record.result.action_text or "No actor action was produced."
            body = f"# Turn {record.result.step}\n\n## Action\n\n{action}"
        return dump_json_envelope(
            schema="story-engine/raw-turn/v1",
            title=title,
            metadata={
                "record_kind": record.record_kind,
                "log_id": record.log_id,
                "parent_log_id": record.parent_log_id,
                "branch_id": record.result.branch_id,
                "session_id": record.result.session_id,
                "step": record.result.step,
                "trace_id": record.trace.trace_id,
                "checkpoint_id": record.checkpoint_id,
            },
            body=body,
            payload=record.model_dump(mode="json"),
        )

    def prepare(self, record: SimulationLogRecord) -> tuple[Path, str]:
        return self.path_for_record(record), self._content(record)

    def append(self, branch_id: str, record: SimulationLogRecord) -> Path:
        if branch_id != record.result.branch_id:
            raise ValueError("simulation log branch mismatch")
        path, content = self.prepare(record)
        with ProjectLock(self.root):
            if path.exists():
                if path.read_text(encoding="utf-8") != content:
                    raise ValueError("conflicting duplicate simulation log")
                return path
            atomic_write_text(path, content, overwrite=False)
        return path

    @staticmethod
    def _sort_key(record: SimulationLogRecord) -> tuple[int, int, object, str]:
        return (
            record.result.step,
            0 if record.record_kind == "genesis" else 1,
            record.trace.started_at,
            record.log_id,
        )

    def read_history(self, branch_id: str) -> tuple[SimulationLogRecord, ...]:
        directory = self.path_for(branch_id)
        if not directory.exists():
            return ()
        records = []
        try:
            for path in directory.glob("*.md"):
                payload = load_json_envelope(path, schema="story-engine/raw-turn/v1")
                record = SimulationLogRecord.model_validate(payload)
                if record.result.branch_id != branch_id:
                    raise ValueError("simulation log stored under the wrong branch")
                records.append(record)
        except (OSError, ValueError) as error:
            raise ValueError(f"simulation log {branch_id!r} is invalid") from error
        records.sort(key=self._sort_key)
        return tuple(records)

    def read(self, branch_id: str) -> tuple[SimulationLogRecord, ...]:
        """Read physical turn files for diagnostics, including abandoned history."""

        return tuple(
            record
            for record in self.read_history(branch_id)
            if record.record_kind == "turn"
        )

    def read_all_history(self) -> tuple[SimulationLogRecord, ...]:
        if not self.directory.exists():
            return ()
        return tuple(
            record
            for branch_dir in sorted(self.directory.iterdir())
            if branch_dir.is_dir()
            for record in self.read_history(branch_dir.name)
        )

    def _branch_ancestry(self, branch_id: str) -> tuple[str, ...]:
        from story_engine.persistence.branch_store import BranchStore

        branches = BranchStore(self.root)
        reverse: list[str] = []
        seen: set[str] = set()
        current = branch_id
        while current not in seen:
            seen.add(current)
            reverse.append(current)
            manifest = branches.load(current)
            if manifest.parent_branch_id is None:
                return tuple(reversed(reverse))
            current = manifest.parent_branch_id
        raise ValueError("branch ancestry contains a cycle")

    def chain(
        self,
        history_head_id: str,
        *,
        branch_id: str,
    ) -> tuple[SimulationLogRecord, ...]:
        records = self.read_all_history()
        by_id: dict[str, SimulationLogRecord] = {}
        for record in records:
            if record.log_id in by_id:
                raise ValueError("duplicate simulation log ID")
            by_id[record.log_id] = record

        reverse: list[SimulationLogRecord] = []
        seen: set[str] = set()
        current: str | None = history_head_id
        while current is not None:
            if current in seen:
                raise ValueError("simulation history contains a cycle or duplicate")
            seen.add(current)
            try:
                record = by_id[current]
            except KeyError as error:
                raise ValueError(
                    f"simulation history log {current!r} is missing"
                ) from error
            reverse.append(record)
            current = record.parent_log_id
        chain = tuple(reversed(reverse))
        if not chain or chain[0].record_kind != "genesis":
            raise ValueError("simulation history does not start at Genesis")
        if any(record.record_kind == "genesis" for record in chain[1:]):
            raise ValueError("simulation history contains duplicate Genesis logs")

        ancestry = self._branch_ancestry(branch_id)
        branch_positions = {item: index for index, item in enumerate(ancestry)}
        positions: list[int] = []
        for record in chain:
            try:
                positions.append(branch_positions[record.result.branch_id])
            except KeyError as error:
                raise ValueError("simulation history branch mismatch") from error
        if positions != sorted(positions):
            raise ValueError("simulation history moves backward across branches")
        return chain

    def reachable(
        self,
        checkpoints: "CheckpointStore",
        checkpoint_id: str,
        *,
        branch_id: str | None = None,
    ) -> tuple[SimulationLogRecord, ...]:
        """Read semantic turns reachable from one checkpoint history head."""

        snapshot = checkpoints.load(checkpoint_id)
        if snapshot.history_head_id is None:
            raise ValueError("checkpoint has no simulation history head")
        return tuple(
            record
            for record in self.chain(
                snapshot.history_head_id,
                branch_id=branch_id or snapshot.branch_id,
            )
            if record.record_kind == "turn"
        )

    def reachable_memory_records(
        self,
        checkpoints: "CheckpointStore",
        checkpoint_id: str,
        *,
        branch_id: str | None = None,
    ) -> tuple[MemoryRecord, ...]:
        snapshot = checkpoints.load(checkpoint_id)
        if snapshot.history_head_id is None:
            raise ValueError("checkpoint has no simulation history head")
        return tuple(
            memory
            for record in self.chain(
                snapshot.history_head_id,
                branch_id=branch_id or snapshot.branch_id,
            )
            for memory in record.memory_delta
        )

    def find_source(self, branch_id: str, source_id: str) -> Path:
        for record in self.read_history(branch_id):
            if any(memory.record_id == source_id for memory in record.memory_delta):
                return self.path_for_record(record)
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
                return self.path_for_record(record)
        raise FileNotFoundError(source_id)
