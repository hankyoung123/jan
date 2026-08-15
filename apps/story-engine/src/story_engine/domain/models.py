from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

JsonScalar = str | int | float | bool | None
CharacterType = Literal["active", "npc", "retired"]
ReviewMode = Literal[
    "submission_review",
    "character_review",
    "world_review",
    "promotion_review",
    "manuscript_review",
]
FactVisibility = Literal["public", "private", "secret"]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class Relationship(DomainModel):
    character_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class Fact(DomainModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    visibility: FactVisibility
    known_by: tuple[str, ...] = ()
    source_event_id: str = Field(min_length=1)
    introduced_at: datetime
    supersedes_fact_id: str | None = None

    @model_validator(mode="after")
    def visibility_has_valid_owners(self) -> Self:
        if self.visibility == "public" and self.known_by:
            raise ValueError("public fact must not have a restricted owner list")
        if self.visibility != "public" and not self.known_by:
            raise ValueError("non-public fact requires at least one knowing character")
        if len(self.known_by) != len(set(self.known_by)):
            raise ValueError("fact known_by character ids must be unique")
        if self.introduced_at.tzinfo is None:
            raise ValueError("introduced_at must include a timezone")
        return self


class InitialFact(DomainModel):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    visibility: FactVisibility = Field(
        description=(
            "Use public for facts known to everyone. Use private or secret only "
            "when knowledge is restricted to specific characters."
        )
    )
    known_by: tuple[str, ...] = Field(
        default=(),
        description=(
            "Must be empty when visibility is public. For private or secret facts, "
            "list every knowing character id exactly once."
        ),
    )
    supersedes_fact_id: str | None = None

    @model_validator(mode="after")
    def visibility_has_valid_owners(self) -> Self:
        if self.visibility == "public" and self.known_by:
            raise ValueError("public initial fact must not have known_by owners")
        if self.visibility != "public" and not self.known_by:
            raise ValueError("non-public initial fact requires a knowing character")
        if len(self.known_by) != len(set(self.known_by)):
            raise ValueError("initial fact known_by ids must be unique")
        return self


class Character(DomainModel):
    id: str = Field(min_length=1)
    display_name: str | None = None
    type: CharacterType
    identity: str = Field(min_length=1)
    core_desire: str = Field(min_length=1)
    current_goal: str | None = None
    known_fact_ids: tuple[str, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    capabilities: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    beliefs: tuple[str, ...] = ()
    location: str | None = None
    emotional_state: str | None = None
    resources: tuple[str, ...] = ()
    version: int = Field(default=0, ge=0)


class WorldClock(DomainModel):
    """A world-owned deadline that may deterministically trigger initiative."""

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    due_at: str = Field(min_length=1)


class WorldState(DomainModel):
    current_time: str = Field(min_length=1)
    current_location: str | None = None
    scene_text: str = ""
    rules: tuple[str, ...] = ()
    active_pressures: tuple[str, ...] = ()
    clocks: tuple[WorldClock, ...] = ()
    public_fact_ids: tuple[str, ...] = ()
    world_variables: dict[str, JsonScalar] = Field(default_factory=dict)
    version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def clock_ids_are_unique(self) -> Self:
        clock_ids = tuple(clock.id for clock in self.clocks)
        if len(clock_ids) != len(set(clock_ids)):
            raise ValueError("world clock IDs must be unique")
        return self


class ReviewIssue(DomainModel):
    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    severity: Literal["warning", "blocking"]
    evidence_ids: tuple[str, ...] = ()


class ReviewResult(DomainModel):
    mode: ReviewMode
    passed: bool
    summary: str = Field(min_length=1)
    issues: tuple[ReviewIssue, ...] = ()

    @model_validator(mode="after")
    def passing_review_has_no_blockers(self) -> Self:
        if self.passed and any(issue.severity == "blocking" for issue in self.issues):
            raise ValueError("passing review cannot contain blocking issues")
        return self
