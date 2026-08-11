import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from threading import Event
from typing import TYPE_CHECKING, Protocol

from pydantic import Field, JsonValue, model_validator

from story_engine.domain.action import ActionSpec, EntityRole
from story_engine.domain.base import Identifier, LocaleCode, RuntimeModel
from story_engine.domain.memory import MemoryBank
from story_engine.domain.models import Character, CharacterType, Fact, WorldState
from story_engine.domain.projection import (
    ResolvedEvent,
    ResolvedTurn,
    SimulationBoundary,
)
from story_engine.domain.recipe import AgentRecipe, PerceptionFrame
from story_engine.domain.trace import TurnTrace

if TYPE_CHECKING:
    from story_engine.persistence.command_store import CommandReceiptCommit


class ActorPhase(StrEnum):
    READY = "ready"
    PRE_ACT = "pre_act"
    POST_ACT = "post_act"
    PRE_OBSERVE = "pre_observe"
    POST_OBSERVE = "post_observe"
    UPDATE = "update"


class StoryActor(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def role(self) -> EntityRole: ...

    def act(self, action_spec: ActionSpec) -> str: ...

    def observe(self, perception: PerceptionFrame | str) -> None: ...

    def get_phase(self) -> ActorPhase: ...

    def get_state(self) -> dict[str, JsonValue]: ...

    def set_state(self, state: Mapping[str, JsonValue]) -> None: ...

    def get_last_log(self) -> Mapping[str, JsonValue]: ...


class GameMasterActor(StoryActor, Protocol):
    def set_resolution_character_registry(self, registry_text: str) -> None: ...

    def set_resolution_world_state(self, world_state_text: str) -> None: ...

    def make_observation(
        self,
        actor: StoryActor,
        *,
        session_id: str,
        step: int,
        content_locale: str,
    ) -> PerceptionFrame: ...

    def select_next_actor(
        self,
        actors: Sequence[StoryActor],
        *,
        session_id: str,
        step: int,
    ) -> str: ...

    def create_action_spec(
        self,
        actor: StoryActor,
        *,
        session_id: str,
        step: int,
        content_locale: str,
    ) -> ActionSpec: ...

    def should_terminate(
        self,
        *,
        session_id: str,
        step: int,
    ) -> tuple[bool, str | None]: ...


class ActorFactory(Protocol):
    def build_actor(
        self,
        recipe: AgentRecipe,
        *,
        actor_params: Mapping[str, str],
        memory: MemoryBank,
        initial_state: Mapping[str, JsonValue] | None = None,
    ) -> StoryActor: ...

    def build_game_master(
        self,
        recipe: AgentRecipe,
        *,
        gm_params: Mapping[str, str],
        actors: Sequence[StoryActor],
        shared_memory: MemoryBank,
        initial_state: Mapping[str, JsonValue] | None = None,
    ) -> GameMasterActor: ...


class ActorStateContext(RuntimeModel):
    """Deterministic, read-only projection of the authoritative Character."""

    id: Identifier
    display_name: str = Field(min_length=1, max_length=256)
    type: CharacterType
    identity: str = Field(min_length=1, max_length=16_384)
    current_goal: str | None = Field(default=None, max_length=16_384)
    location: str | None = Field(default=None, max_length=1024)
    capabilities: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    beliefs: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()

    @classmethod
    def from_character(cls, character: Character) -> "ActorStateContext":
        return cls(
            id=character.id,
            display_name=character.display_name or character.id,
            type=character.type,
            identity=character.identity,
            current_goal=character.current_goal,
            location=character.location,
            capabilities=character.capabilities,
            conditions=character.conditions,
            resources=character.resources,
            beliefs=character.beliefs,
            relationships=tuple(
                relationship.description
                for relationship in character.relationships
            ),
        )

    def prompt_text(self) -> str:
        return json.dumps(
            self.model_dump(
                mode="json",
                exclude={"id", "display_name", "type"},
            ),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class ResolverContext(RuntimeModel):
    """One authoritative, bounded input to Game Master resolution.

    Canonical facts and actor knowledge are explicit so the Wiki remains a
    semantic aid rather than an accidental source of world truth.
    """

    session_id: Identifier
    branch_id: Identifier
    step: int = Field(ge=0)
    acting_actor_id: Identifier
    putative_event_text: str = Field(min_length=1, max_length=65_536)
    content_locale: LocaleCode
    existing_characters: tuple[ActorStateContext, ...] = Field(min_length=1)
    world_time: str | None = Field(default=None, max_length=1_024)
    world_location: str | None = Field(default=None, max_length=1_024)
    world_rules: tuple[str, ...] = ()
    world_active_pressures: tuple[str, ...] = ()
    world_variables: dict[str, JsonValue] = Field(default_factory=dict)
    relevant_canonical_facts: tuple[Fact, ...] = ()
    actor_known_facts: tuple[Fact, ...] = ()
    actor_observed_events: tuple[str, ...] = ()
    recent_resolved_events: tuple[str, ...] = ()
    wiki_context: str = Field(default="", max_length=32_768)

    @model_validator(mode="after")
    def existing_character_ids_are_valid(self) -> "ResolverContext":
        character_ids = tuple(character.id for character in self.existing_characters)
        if len(character_ids) != len(set(character_ids)):
            raise ValueError("existing character IDs must be unique")
        if self.acting_actor_id not in character_ids:
            raise ValueError("acting actor must exist in the character registry")
        canonical_ids = tuple(fact.id for fact in self.relevant_canonical_facts)
        if len(canonical_ids) != len(set(canonical_ids)):
            raise ValueError("relevant canonical fact IDs must be unique")
        known_ids = tuple(fact.id for fact in self.actor_known_facts)
        if len(known_ids) != len(set(known_ids)):
            raise ValueError("actor known fact IDs must be unique")
        if not set(known_ids).issubset(canonical_ids):
            raise ValueError("actor known facts must be included in canonical truth")
        return self


class ResolverKernel(Protocol):
    def resolve(
        self,
        game_master: GameMasterActor,
        context: ResolverContext,
        *,
        cancellation: Event,
    ) -> ResolvedTurn: ...


class ControlMode(StrEnum):
    STEP = "step"
    SCENE = "scene"
    CHAPTER = "chapter"
    AUTONOMOUS = "autonomous"


class ControlPolicy(RuntimeModel):
    mode: ControlMode
    pause_after_scene: bool = True
    max_steps: int = Field(default=40, ge=1, le=10_000)
    max_scenes: int = Field(default=1, ge=1, le=1_000)
    max_total_tokens: int = Field(default=500_000, ge=1)
    max_runtime_seconds: int = Field(default=3_600, ge=1)
    max_consecutive_model_failures: int = Field(default=3, ge=1, le=20)
    allow_user_override: bool = True


class ManuscriptGenerationMode(StrEnum):
    MANUAL = "manual"
    AFTER_SCENE = "after_scene"
    AFTER_CHAPTER = "after_chapter"


class WikiMaintenanceMode(StrEnum):
    MANUAL = "manual"
    AFTER_SCENE = "after_scene"
    AFTER_CHAPTER = "after_chapter"


class OutputPolicy(RuntimeModel):
    manuscript_mode: ManuscriptGenerationMode = ManuscriptGenerationMode.MANUAL
    wiki_mode: WikiMaintenanceMode = WikiMaintenanceMode.AFTER_SCENE


class TurnSessionStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    PAUSED = "paused"
    TERMINATED = "terminated"
    CANCELLED = "cancelled"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class PendingControl(StrEnum):
    NONE = "none"
    PAUSE = "pause"
    TERMINATE = "terminate"


class TurnSessionRequest(RuntimeModel):
    project_id: Identifier
    branch_id: Identifier
    premise_text: str = Field(min_length=1, max_length=131_072)
    # The request identifies the eligible cast; scene participation is capped
    # separately by the roster planner.
    actor_ids: tuple[Identifier, ...] = ()
    player_actor_id: Identifier | None = None
    content_locale: LocaleCode
    control: ControlPolicy
    output: OutputPolicy = OutputPolicy()
    seed: int | None = None


class PromotionProposal(RuntimeModel):
    """Model-written promotion semantics; the candidate ID is local context."""

    promote: bool
    proposed_goal: str | None = Field(default=None, max_length=16_384)
    reason: str = Field(min_length=1, max_length=16_384)

    @model_validator(mode="after")
    def validate_proposal(self) -> "PromotionProposal":
        if self.promote and not self.proposed_goal:
            raise ValueError("promotion requires a goal")
        if not self.promote and self.proposed_goal is not None:
            raise ValueError("a rejected promotion cannot propose a goal")
        return self


class PromotionDecision(RuntimeModel):
    """Durable promotion result after local candidate binding."""

    character_id: Identifier
    promote: bool
    proposed_goal: str | None = Field(default=None, max_length=16_384)
    evidence_event_ids: tuple[Identifier, ...] = ()
    reason: str = Field(min_length=1, max_length=16_384)

    @model_validator(mode="after")
    def validate_decision(self) -> "PromotionDecision":
        PromotionProposal.model_validate(
            self.model_dump(exclude={"character_id", "evidence_event_ids"})
        )
        return self


class TurnSessionSnapshot(RuntimeModel):
    session_id: Identifier
    project_id: Identifier
    branch_id: Identifier
    status: TurnSessionStatus
    pending_control: PendingControl = PendingControl.NONE
    content_locale: LocaleCode
    request: TurnSessionRequest
    player_actor_id: Identifier | None = None
    world: WorldState | None = None
    roster_actor_ids: tuple[Identifier, ...] = Field(default=(), max_length=4)
    characters: tuple[Character, ...] = ()
    pending_scene_events: tuple[ResolvedEvent, ...] = ()
    current_step: int = Field(ge=0)
    completed_scenes: int = Field(default=0, ge=0)
    active_actor_id: Identifier | None = None
    current_action_spec: ActionSpec | None = None
    actor_states: dict[Identifier, dict[str, JsonValue]]
    game_master_states: dict[Identifier, dict[str, JsonValue]]
    raw_log_offset: int = Field(ge=0)
    total_model_tokens: int = Field(default=0, ge=0)
    consecutive_model_failures: int = Field(default=0, ge=0)
    history_head_id: str | None = Field(
        default=None,
        pattern=r"^log-[0-9a-f]{64}$",
    )
    checkpoint_id: Identifier | None = None
    started_at: datetime
    updated_at: datetime
    termination_reason_text: str | None = None
    restoration_notice_text: str | None = None
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class StepResult(RuntimeModel):
    session_id: Identifier
    branch_id: Identifier
    step: int = Field(ge=0)
    acting_actor_id: Identifier | None
    action_spec: ActionSpec | None
    action_text: str | None
    resolved_turn: ResolvedTurn | None
    status: TurnSessionStatus
    boundary: SimulationBoundary = SimulationBoundary.NONE
    follow_up_actor_ids: tuple[Identifier, ...] = Field(default=(), max_length=3)
    promotion_decisions: tuple[PromotionDecision, ...] = ()
    checkpoint_id: Identifier | None = None


class TurnEngine(Protocol):
    def create_session(self, request: TurnSessionRequest) -> TurnSessionSnapshot: ...

    def advance_one_step(
        self,
        session_id: str,
        *,
        cancellation: Event,
    ) -> StepResult: ...

    def pause(self, session_id: str) -> TurnSessionSnapshot: ...

    def terminate(
        self,
        session_id: str,
        *,
        reason_text: str,
    ) -> TurnSessionSnapshot: ...

    def restore(self, snapshot: TurnSessionSnapshot) -> TurnSessionSnapshot: ...


class BranchManifest(RuntimeModel):
    branch_id: Identifier
    project_id: Identifier
    parent_branch_id: Identifier | None = None
    fork_checkpoint_id: Identifier | None = None
    head_checkpoint_id: Identifier | None = None
    head_step: int = Field(default=0, ge=0)
    content_locale: LocaleCode
    created_at: datetime
    updated_at: datetime


class CommitResult(RuntimeModel):
    branch: BranchManifest
    checkpoint_id: Identifier
    history_head_id: str = Field(pattern=r"^log-[0-9a-f]{64}$")
    session_id: Identifier
    step: int = Field(ge=0)
    state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    written_paths: tuple[str, ...]


class CommitKernel(Protocol):
    def append_step(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
        trace: TurnTrace,
        *,
        checkpoint: bool = True,
        command_receipt: "CommandReceiptCommit | None" = None,
    ) -> CommitResult | None: ...

    def save_checkpoint(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        reason: str,
    ) -> CommitResult: ...

    def load_checkpoint(
        self,
        project_id: str,
        checkpoint_id: str,
    ) -> TurnSessionSnapshot: ...

    def create_branch(
        self,
        project_id: str,
        *,
        source_checkpoint_id: str,
        branch_id: str,
    ) -> BranchManifest: ...

    def rollback_branch(
        self,
        project_id: str,
        branch_id: str,
        *,
        checkpoint_id: str,
    ) -> BranchManifest: ...
