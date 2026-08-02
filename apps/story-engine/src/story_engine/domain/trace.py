from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from story_engine.domain.action import ActionSpec, TaskType
from story_engine.domain.base import Identifier, LocaleCode, RuntimeModel


class ModelCallStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class ModelCallTrace(RuntimeModel):
    call_id: Identifier
    task_id: Identifier
    task_type: TaskType
    status: ModelCallStatus
    session_id: Identifier | None = None
    branch_id: Identifier | None = None
    step: int | None = Field(default=None, ge=0)
    actor_id: Identifier | None = None
    profile_id: Identifier
    provider_id: Identifier
    model_id: str
    prompt_version: Identifier
    content_locale: LocaleCode
    component_ids: tuple[Identifier, ...] = ()
    source_record_ids: tuple[Identifier, ...] = ()
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    duration_ms: int = Field(ge=0)
    retry_count: int = Field(default=0, ge=0)
    error_code: Identifier | None = None
    validation_errors: tuple[str, ...] = ()
    started_at: datetime
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def timestamps_are_valid(self) -> Self:
        if self.started_at.tzinfo is None:
            raise ValueError("started_at must include a timezone")
        if self.completed_at is not None:
            if self.completed_at.tzinfo is None:
                raise ValueError("completed_at must include a timezone")
            if self.completed_at < self.started_at:
                raise ValueError("completed_at cannot precede started_at")
        return self


class StageTrace(RuntimeModel):
    stage_id: Identifier
    stage_type: Identifier
    started_at: datetime
    completed_at: datetime | None = None
    status: ModelCallStatus
    model_call_ids: tuple[Identifier, ...] = ()
    input_record_ids: tuple[Identifier, ...] = ()
    output_record_ids: tuple[Identifier, ...] = ()
    detail_text: str | None = None


class TurnTrace(RuntimeModel):
    trace_id: Identifier
    session_id: Identifier
    branch_id: Identifier
    step: int = Field(ge=0)
    content_locale: LocaleCode
    stages: tuple[StageTrace, ...]
    model_calls: tuple[ModelCallTrace, ...]
    action_spec: ActionSpec | None = None
    acting_actor_id: Identifier | None = None
    putative_event_record_id: Identifier | None = None
    resolved_event_record_ids: tuple[Identifier, ...] = ()
    started_at: datetime
    completed_at: datetime | None = None
    status: ModelCallStatus
