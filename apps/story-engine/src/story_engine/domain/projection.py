from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, JsonValue, model_validator

from story_engine.domain.action import ActionSpec
from story_engine.domain.base import Identifier, LocaleCode, RuntimeModel


class EffectOperation(StrEnum):
    SET = "set"
    APPEND = "append"
    REMOVE = "remove"
    CREATE_CHARACTER = "create_character"
    EMIT_SIGNAL = "emit_signal"


class EffectTarget(StrEnum):
    WORLD_PROJECTION = "world_projection"
    CHARACTER_PROJECTION = "character_projection"
    SYSTEM = "system"


class StateEffect(RuntimeModel):
    """Optional machine projection; natural-language events remain authoritative."""

    effect_id: Identifier
    operation: EffectOperation
    target: EffectTarget
    target_id: Identifier | None = None
    path: str | None = Field(default=None, max_length=512)
    before: JsonValue = None
    after: JsonValue = None
    reason_text: str | None = Field(default=None, max_length=16_384)
    required: bool = False
    source_record_ids: tuple[Identifier, ...] = ()


class EventVisibility(StrEnum):
    PUBLIC = "public"
    PARTICIPANTS = "participants"
    RESTRICTED = "restricted"
    GM_ONLY = "gm_only"


class SimulationBoundary(StrEnum):
    NONE = "none"
    SCENE = "scene"
    CHAPTER = "chapter"


class EntityChange(RuntimeModel):
    """One ordinary NPC introduced by the Game Master resolution envelope."""

    entity_id: Identifier
    display_name: str | None = Field(default=None, max_length=256)
    identity: str | None = Field(default=None, max_length=16_384)
    core_desire: str | None = Field(default=None, max_length=16_384)
    location: str | None = Field(default=None, max_length=1024)

    @model_validator(mode="after")
    def validate_required_fields(self) -> Self:
        if not self.display_name or not self.identity or not self.core_desire:
            raise ValueError(
                "create_npc requires display_name, identity and core_desire"
            )
        return self


class ResolutionEnvelope(RuntimeModel):
    """Structured Game Master resolution for one putative action."""

    event_text: str = Field(min_length=1, max_length=65_536)
    boundary: SimulationBoundary
    visibility: EventVisibility
    observer_ids: tuple[Identifier, ...] = ()
    participant_ids: tuple[Identifier, ...] = ()
    entity_changes: tuple[EntityChange, ...] = ()


class ResolvedEvent(RuntimeModel):
    """Lightweight projection of a Game Master world event."""

    event_id: Identifier
    session_id: Identifier
    step: int = Field(ge=0)
    actor_id: Identifier | None = None
    event_text: str = Field(min_length=1, max_length=65_536)
    visibility: EventVisibility
    observer_ids: tuple[Identifier, ...] = ()
    participant_ids: tuple[Identifier, ...] = ()
    location_ids: tuple[Identifier, ...] = ()
    source_intent_ids: tuple[Identifier, ...] = ()
    source_memory_ids: tuple[Identifier, ...] = ()
    effects: tuple[StateEffect, ...] = ()
    content_locale: LocaleCode
    occurred_at: datetime
    importance: float = Field(default=0.5, ge=0, le=1)
    confidence: float = Field(default=1.0, ge=0, le=1)

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        if self.visibility == EventVisibility.RESTRICTED and not self.observer_ids:
            raise ValueError("restricted event requires observer_ids")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return self


class ResolvedTurn(RuntimeModel):
    """Project-side projection of one Engine step."""

    session_id: Identifier
    branch_id: Identifier
    step: int = Field(ge=0)
    acting_actor_id: Identifier | None = None
    putative_event_text: str | None = Field(default=None, max_length=65_536)
    raw_resolution_text: str = Field(min_length=1, max_length=131_072)
    events: tuple[ResolvedEvent, ...] = ()
    effects: tuple[StateEffect, ...] = ()
    action_spec: ActionSpec | None = None
    terminated: bool = False
    boundary: SimulationBoundary = SimulationBoundary.NONE
    termination_reason_text: str | None = Field(default=None, max_length=16_384)
    content_locale: LocaleCode
