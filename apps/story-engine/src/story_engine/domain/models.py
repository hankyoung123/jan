from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from story_engine.domain.errors import InvalidTransitionError

JsonScalar = str | int | float | bool | None
CharacterType = Literal["active", "npc", "retired"]
TurnStatus = Literal[
    "draft",
    "reviewed",
    "needs_revision",
    "approved",
    "discarded",
    "committed",
]
PromotionStatus = Literal["pending", "committed"]
ReviewMode = Literal[
    "submission_review",
    "character_review",
    "world_review",
    "turn_review",
    "promotion_review",
    "manuscript_review",
]


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class Relationship(DomainModel):
    character_id: str = Field(min_length=1)
    description: str = Field(min_length=1)


class StateChange(DomainModel):
    target_type: Literal["character", "world"]
    target_id: str = Field(min_length=1)
    field: str = Field(min_length=1)
    old_value: JsonValue = None
    new_value: JsonValue
    reason: str = Field(min_length=1)


class Character(DomainModel):
    id: str = Field(min_length=1)
    display_name: str | None = None
    type: CharacterType
    identity: str = Field(min_length=1)
    core_desire: str = Field(min_length=1)
    current_goal: str | None = None
    known_fact_ids: tuple[str, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    location: str | None = None
    emotional_state: str | None = None
    resources: tuple[str, ...] = ()
    last_event_id: str | None = None
    version: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def active_character_has_goal(self) -> Self:
        if self.type == "active" and not self.current_goal:
            raise ValueError("active character requires a current goal")
        return self


class WorldState(DomainModel):
    current_time: str = Field(min_length=1)
    current_location: str | None = None
    rules: tuple[str, ...] = ()
    active_pressures: tuple[str, ...] = ()
    public_fact_ids: tuple[str, ...] = ()
    world_variables: dict[str, JsonScalar] = Field(default_factory=dict)
    version: int = Field(default=0, ge=0)


class CharacterIntent(DomainModel):
    character_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    target: str | None = None
    goal: str = Field(min_length=1)
    knowledge_basis: tuple[str, ...] = Field(min_length=1)
    recognized_risk: str | None = None


class NpcCandidate(DomainModel):
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    identity: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    current_goal: str | None = None


class WorldOutcome(DomainModel):
    summary: str = Field(min_length=1)
    public_results: tuple[str, ...] = ()
    hidden_results: tuple[str, ...] = ()
    character_changes: tuple[StateChange, ...] = ()
    world_changes: tuple[StateChange, ...] = ()
    new_npcs: tuple[NpcCandidate, ...] = ()
    unresolved_consequences: tuple[str, ...] = ()

    @model_validator(mode="after")
    def npc_ids_are_unique(self) -> Self:
        npc_ids = [npc.id for npc in self.new_npcs]
        if len(npc_ids) != len(set(npc_ids)):
            raise ValueError("NPC IDs must be unique within one outcome")
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


class PromotionCandidate(DomainModel):
    id: str = Field(pattern=r"^promotion-[a-z0-9][a-z0-9-]*-v[0-9]+$")
    project_id: str = Field(min_length=1)
    character_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    base_character_version: int = Field(ge=0)
    proposed_goal: str = Field(min_length=1)
    review: ReviewResult
    status: PromotionStatus = "pending"

    @model_validator(mode="after")
    def requires_passing_promotion_review(self) -> Self:
        if self.review.mode != "promotion_review" or not self.review.passed:
            raise ValueError("promotion candidate requires a passing promotion review")
        return self

    def mark_committed(self) -> Self:
        if self.status != "pending":
            raise InvalidTransitionError("only a pending promotion can be committed")
        return self.model_copy(update={"status": "committed"})


class StoryEvent(DomainModel):
    id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    occurred_at: datetime
    summary: str = Field(min_length=1)
    participants: tuple[str, ...]
    public_results: tuple[str, ...] = ()
    hidden_results: tuple[str, ...] = ()
    character_changes: tuple[StateChange, ...] = ()
    world_changes: tuple[StateChange, ...] = ()
    source_turn_id: str = Field(min_length=1)
    approved_by_user: bool

    @model_validator(mode="after")
    def formal_event_is_approved_and_timezone_aware(self) -> Self:
        if not self.approved_by_user:
            raise ValueError("formal story event requires user approval")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")
        return self


class TurnCandidate(DomainModel):
    id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    base_world_version: int = Field(ge=0)
    base_character_versions: dict[str, int]
    intents: tuple[CharacterIntent, ...] = Field(min_length=1)
    outcome: WorldOutcome
    review: ReviewResult | None = None
    status: TurnStatus = "draft"

    @model_validator(mode="after")
    def lifecycle_state_is_consistent(self) -> Self:
        if self.status == "reviewed" and self.review is None:
            raise ValueError("reviewed candidate requires a review")
        if self.status == "approved" and (
            self.review is None or not self.review.passed
        ):
            raise ValueError("approved candidate requires a passing review")
        return self

    def with_review(self, review: ReviewResult) -> Self:
        if self.status in {"discarded", "committed"}:
            raise InvalidTransitionError(
                f"cannot review a candidate with status {self.status}"
            )
        status: TurnStatus = "reviewed" if review.passed else "needs_revision"
        return self.model_copy(update={"review": review, "status": status})

    def with_outcome(self, outcome: WorldOutcome) -> Self:
        if self.status in {"discarded", "committed"}:
            raise InvalidTransitionError(
                f"cannot edit a candidate with status {self.status}"
            )
        return self.model_copy(
            update={"outcome": outcome, "review": None, "status": "draft"}
        )

    def approve(self) -> Self:
        if self.status != "reviewed" or self.review is None or not self.review.passed:
            raise InvalidTransitionError(
                "candidate approval requires a passing current review"
            )
        return self.model_copy(update={"status": "approved"})

    def discard(self) -> Self:
        if self.status == "committed":
            raise InvalidTransitionError("a committed candidate cannot be discarded")
        return self.model_copy(update={"status": "discarded"})

    def mark_committed(self) -> Self:
        if self.status != "approved":
            raise InvalidTransitionError("only an approved candidate can be committed")
        return self.model_copy(update={"status": "committed"})
