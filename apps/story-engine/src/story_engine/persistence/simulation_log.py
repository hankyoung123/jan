import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from story_engine.domain.simulation import StepResult
from story_engine.domain.trace import TurnTrace
from story_engine.workspace.lock import ProjectLock

_BRANCH_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,127}$")


class SimulationLogRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    checkpoint_id: str = Field(pattern=r"^checkpoint-[0-9a-f]{64}$")
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    result: StepResult
    trace: TurnTrace


class SimulationLogStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / ".story-engine/runtime/logs"

    def path_for(self, branch_id: str) -> Path:
        if not _BRANCH_ID.fullmatch(branch_id):
            raise ValueError("invalid branch ID")
        return self.directory / f"{branch_id}.jsonl"

    def append(self, branch_id: str, record: SimulationLogRecord) -> Path:
        path = self.path_for(branch_id)
        content = json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with ProjectLock(self.root):
            existing = self.read(branch_id)
            duplicate = next(
                (
                    item
                    for item in existing
                    if item.result.session_id == record.result.session_id
                    and item.result.step == record.result.step
                ),
                None,
            )
            if duplicate is not None:
                if duplicate != record:
                    raise ValueError("conflicting duplicate simulation log step")
                return path
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{content}\n")
                handle.flush()
                os.fsync(handle.fileno())
        return path

    def read(self, branch_id: str) -> tuple[SimulationLogRecord, ...]:
        path = self.path_for(branch_id)
        if not path.exists():
            return ()
        records = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    payload: Any = json.loads(line)
                    records.append(SimulationLogRecord.model_validate(payload))
        except (OSError, ValueError) as error:
            raise ValueError(f"simulation log {branch_id!r} is invalid") from error
        latest_step_by_session: dict[str, int] = {}
        for record in records:
            previous_step = latest_step_by_session.get(record.result.session_id)
            if previous_step is not None and record.result.step <= previous_step:
                raise ValueError("simulation log steps must be strictly increasing")
            latest_step_by_session[record.result.session_id] = record.result.step
        return tuple(records)
