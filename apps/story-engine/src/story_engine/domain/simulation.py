from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from threading import Event
from typing import Protocol

from pydantic import Field, JsonValue

from story_engine.domain.action import ActionSpec, EntityRole
from story_engine.domain.base import Identifier, LocaleCode, RuntimeModel
from story_engine.domain.memory import MemoryBank, MemorySnapshot
from story_engine.domain.projection import ResolvedTurn, SimulationBoundary
from story_engine.domain.recipe import AgentRecipe, PerceptionFrame
from story_engine.domain.trace import TurnTrace


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
    def role(self) -> EntityRole: ...

    def act(self, action_spec: ActionSpec) -> str: ...

    def observe(self, perception: PerceptionFrame | str) -> None: ...

    def get_phase(self) -> ActorPhase: ...

    def get_state(self) -> dict[str, JsonValue]: ...

    def set_state(self, state: Mapping[str, JsonValue]) -> None: ...

    def get_last_log(self) -> Mapping[str, JsonValue]: ...


class GameMasterActor(StoryActor, Protocol):
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


class ResolverContext(RuntimeModel):
    session_id: Identifier
    branch_id: Identifier
    step: int = Field(ge=0)
    acting_actor_id: Identifier
    putative_event_text: str = Field(min_length=1, max_length=65_536)
    content_locale: LocaleCode


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
    allow_dynamic_entities: bool = True
    allow_user_override: bool = True
    checkpoint_every_steps: int = Field(default=1, ge=1, le=1_000)


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
    actor_ids: tuple[Identifier, ...] = ()
    content_locale: LocaleCode
    control: ControlPolicy
    output: OutputPolicy = OutputPolicy()
    seed: int | None = None


class DynamicEntityDefinition(RuntimeModel):
    entity_id: Identifier
    display_name: str = Field(min_length=1, max_length=256)
    identity: str = Field(min_length=1, max_length=16_384)
    goal: str = Field(min_length=1, max_length=16_384)
    location: str | None = Field(default=None, max_length=1_024)
    active: bool = True


class TurnSessionSnapshot(RuntimeModel):
    session_id: Identifier
    project_id: Identifier
    branch_id: Identifier
    status: TurnSessionStatus
    pending_control: PendingControl = PendingControl.NONE
    content_locale: LocaleCode
    request: TurnSessionRequest
    resolved_model_profile_ids: dict[Identifier, Identifier] = Field(
        default_factory=dict
    )
    active_entity_ids: tuple[Identifier, ...] = ()
    dynamic_entities: tuple[DynamicEntityDefinition, ...] = ()
    current_step: int = Field(ge=0)
    completed_scenes: int = Field(default=0, ge=0)
    active_actor_id: Identifier | None = None
    current_action_spec: ActionSpec | None = None
    actor_states: dict[Identifier, dict[str, JsonValue]]
    game_master_states: dict[Identifier, dict[str, JsonValue]]
    memory_snapshots: dict[Identifier, MemorySnapshot]
    raw_log_offset: int = Field(ge=0)
    total_model_tokens: int = Field(default=0, ge=0)
    consecutive_model_failures: int = Field(default=0, ge=0)
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
    checkpoint_id: Identifier | None = None


class TurnEngine(Protocol):
    def create_session(self, request: TurnSessionRequest) -> TurnSessionSnapshot: ...

    def run(
        self,
        session_id: str,
        *,
        cancellation: Event,
        on_step: Callable[[StepResult], None] | None = None,
    ) -> TurnSessionSnapshot: ...

    def step(self, session_id: str, *, cancellation: Event) -> StepResult: ...

    def pause(self, session_id: str) -> TurnSessionSnapshot: ...

    def resume(
        self,
        session_id: str,
        *,
        cancellation: Event,
    ) -> TurnSessionSnapshot: ...

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

    def project_markdown(
        self,
        project_id: str,
        branch_id: str,
        *,
        checkpoint_id: str | None = None,
    ) -> tuple[Path, ...]: ...
