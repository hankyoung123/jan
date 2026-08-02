from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Protocol, Self

from pydantic import Field, JsonValue, model_validator

from story_engine.domain.base import Identifier, LocaleCode, RuntimeModel


class MemoryRecordType(StrEnum):
    PREMISE = "premise"
    OBSERVATION = "observation"
    PUTATIVE_EVENT = "putative_event"
    WORLD_EVENT = "world_event"
    REFLECTION = "reflection"
    CONSOLIDATION = "consolidation"
    PLAN = "plan"
    SYSTEM = "system"


class MemoryScope(StrEnum):
    GAME_MASTER = "game_master"
    CHARACTER = "character"
    SHARED = "shared"


class MemoryRecord(RuntimeModel):
    record_id: Identifier
    record_type: MemoryRecordType
    scope: MemoryScope
    owner_id: Identifier
    session_id: Identifier
    branch_id: Identifier
    step: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=131_072)
    content_locale: LocaleCode
    created_at: datetime
    actor_ids: tuple[Identifier, ...] = ()
    location_ids: tuple[Identifier, ...] = ()
    source_record_ids: tuple[Identifier, ...] = ()
    tags: tuple[Identifier, ...] = ()
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)
    visible_to: tuple[Identifier, ...] = ()
    raw_text: str | None = Field(default=None, max_length=131_072)

    @model_validator(mode="after")
    def created_at_is_timezone_aware(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return self


class MemoryQuery(RuntimeModel):
    query_text: str = Field(min_length=1, max_length=32_768)
    limit: int = Field(default=8, ge=1, le=100)
    record_types: tuple[MemoryRecordType, ...] = ()
    actor_ids: tuple[Identifier, ...] = ()
    location_ids: tuple[Identifier, ...] = ()
    tags: tuple[Identifier, ...] = ()
    min_importance: float = Field(default=0, ge=0, le=1)
    before_step: int | None = Field(default=None, ge=0)
    content_locale: LocaleCode | None = None


class MemoryHit(RuntimeModel):
    record: MemoryRecord
    score: float
    semantic_score: float | None = None
    recency_score: float | None = None
    importance_score: float | None = None


class MemorySnapshot(RuntimeModel):
    owner_id: Identifier
    scope: MemoryScope
    state: dict[str, JsonValue]
    record_count: int = Field(ge=0)
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class MemoryBank(Protocol):
    @property
    def owner_id(self) -> str: ...

    def add(self, record: MemoryRecord) -> None: ...

    def extend(self, records: Iterable[MemoryRecord]) -> None: ...

    def retrieve(self, query: MemoryQuery) -> Sequence[MemoryHit]: ...

    def retrieve_recent(
        self,
        *,
        limit: int,
        record_types: tuple[MemoryRecordType, ...] = (),
    ) -> Sequence[MemoryRecord]: ...

    def scan(
        self,
        predicate: Callable[[MemoryRecord], bool],
    ) -> Sequence[MemoryRecord]: ...

    def flush(self) -> None: ...

    def snapshot(self) -> MemorySnapshot: ...

    def restore(self, snapshot: MemorySnapshot) -> None: ...


class MemoryCodec(Protocol):
    def encode(self, record: MemoryRecord) -> str: ...

    def decode(self, value: str) -> MemoryRecord | None: ...
