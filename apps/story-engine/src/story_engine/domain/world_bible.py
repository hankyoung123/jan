from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from story_engine.domain.base import Identifier, RuntimeModel


class WorldBibleCategory(StrEnum):
    RULE = "rule"
    LOCATION = "location"
    ORGANIZATION = "organization"
    HISTORY = "history"
    ESTABLISHED_FACT = "established_fact"
    UNRESOLVED_THREAD = "unresolved_thread"


class WorldBibleEntry(RuntimeModel):
    entry_id: Identifier
    category: WorldBibleCategory
    title: str = Field(min_length=1, max_length=512)
    content_text: str = Field(min_length=1, max_length=65_536)
    source_record_ids: tuple[Identifier, ...]
    source_event_ids: tuple[Identifier, ...]
    first_seen_step: int = Field(ge=0)
    last_updated_step: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    status: Literal["active", "historical", "uncertain"]

    @model_validator(mode="after")
    def update_step_follows_first_seen(self) -> Self:
        if self.last_updated_step < self.first_seen_step:
            raise ValueError("world bible update precedes first appearance")
        return self


class WorldBibleSnapshot(RuntimeModel):
    project_id: Identifier
    branch_id: Identifier
    checkpoint_id: Identifier
    creative_direction_text: str
    current_world_state_text: str
    rules: tuple[WorldBibleEntry, ...]
    locations: tuple[WorldBibleEntry, ...]
    organizations: tuple[WorldBibleEntry, ...]
    history: tuple[WorldBibleEntry, ...]
    established_facts: tuple[WorldBibleEntry, ...]
    unresolved_threads: tuple[WorldBibleEntry, ...]
    generated_at: datetime

    @model_validator(mode="after")
    def generated_at_is_timezone_aware(self) -> Self:
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must include a timezone")
        for name, category in (
            ("rules", WorldBibleCategory.RULE),
            ("locations", WorldBibleCategory.LOCATION),
            ("organizations", WorldBibleCategory.ORGANIZATION),
            ("history", WorldBibleCategory.HISTORY),
            ("established_facts", WorldBibleCategory.ESTABLISHED_FACT),
            ("unresolved_threads", WorldBibleCategory.UNRESOLVED_THREAD),
        ):
            if any(item.category != category for item in getattr(self, name)):
                raise ValueError(f"{name} contains an entry from another category")
        return self


class DirectorInstruction(RuntimeModel):
    instruction_id: Identifier
    text: str = Field(min_length=1, max_length=65_536)
    created_at: datetime
    applies_from_checkpoint_id: Identifier

    @model_validator(mode="after")
    def created_at_is_timezone_aware(self) -> Self:
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return self
