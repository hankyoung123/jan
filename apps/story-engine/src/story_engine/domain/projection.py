from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal, Self

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

    def merge(self, other: "SimulationBoundary") -> "SimulationBoundary":
        """Keep the stronger boundary: NONE < SCENE < CHAPTER."""
        if self == SimulationBoundary.CHAPTER or other == SimulationBoundary.CHAPTER:
            return SimulationBoundary.CHAPTER
        if self == SimulationBoundary.SCENE or other == SimulationBoundary.SCENE:
            return SimulationBoundary.SCENE
        return SimulationBoundary.NONE


class ProjectionKind(StrEnum):
    WIKI = "wiki"
    MANUSCRIPT = "manuscript"


class ProjectionTaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class ProjectionTask(RuntimeModel):
    """A rebuildable view update derived from one committed simulation boundary."""

    task_id: Identifier
    project_id: Identifier
    session_id: Identifier
    branch_id: Identifier
    checkpoint_id: Identifier
    step: int = Field(ge=0)
    boundary: SimulationBoundary
    kind: ProjectionKind
    status: ProjectionTaskStatus = ProjectionTaskStatus.PENDING
    attempt_count: int = Field(default=0, ge=0)
    error_text: str | None = Field(default=None, max_length=16_384)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def timestamps_are_valid(self) -> "ProjectionTask":
        if self.updated_at < self.created_at:
            raise ValueError("projection task update precedes creation")
        if self.completed_at is not None and self.completed_at < self.created_at:
            raise ValueError("projection task completion precedes creation")
        return self


class EntityChange(RuntimeModel):
    """One ordinary NPC introduced by the Game Master resolution envelope."""

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


class ResolutionStateUpdate(RuntimeModel):
    """Minimal GM-authored state change, bound to local store-owned paths."""

    target: Literal[
        EffectTarget.WORLD_PROJECTION,
        EffectTarget.CHARACTER_PROJECTION,
    ]
    target_name: str | None = Field(default=None, max_length=256)
    path: Literal[
        "location",
        "conditions",
        "resources",
        "beliefs",
        "current_goal",
        "current_time",
        "current_location",
    ]
    value: JsonValue

    @model_validator(mode="after")
    def uses_a_supported_store_owned_path(self) -> Self:
        character_paths = {
            "location",
            "conditions",
            "resources",
            "beliefs",
            "current_goal",
        }
        if self.target == EffectTarget.CHARACTER_PROJECTION:
            if not self.target_name:
                raise ValueError("character state update requires a target name")
            if self.path not in character_paths:
                raise ValueError("character state update path is not supported")
        elif self.target == EffectTarget.WORLD_PROJECTION:
            if self.target_name is not None:
                raise ValueError("world state update must not name a character")
            if self.path not in {"current_time", "current_location"}:
                raise ValueError("world state update path is not supported")
        else:
            raise ValueError("resolution state updates cannot target system state")
        return self


class ResolutionEnvelope(RuntimeModel):
    """Semantic Game Master resolution for one putative action.

    Character identifiers are deliberately absent: the resolver maps the names
    in this model output onto its local character registry before persisting an
    event.
    """

    event_text: str = Field(min_length=1, max_length=65_536)
    boundary: SimulationBoundary
    visibility: EventVisibility
    observer_names: tuple[str, ...] = Field(default=(), max_length=64)
    participant_names: tuple[str, ...] = Field(default=(), max_length=64)
    response_actor_names: tuple[str, ...] = Field(
        default=(),
        max_length=64,
        description=(
            "Characters whose own Actors may take an immediate voluntary "
            "follow-up after this event. This is independent of observation "
            "and participation."
        ),
    )
    entity_changes: tuple[EntityChange, ...] = ()
    state_updates: tuple[ResolutionStateUpdate, ...] = ()


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
    response_actor_ids: tuple[Identifier, ...] = ()
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
