from enum import StrEnum
from typing import Self

from pydantic import Field, JsonValue, model_validator

from story_engine.domain.base import Identifier, LocaleCode, RuntimeModel


class EntityRole(StrEnum):
    CHARACTER = "character"
    GAME_MASTER = "game_master"
    INITIALIZER = "initializer"
    DIRECTOR = "director"


class ActionOutputType(StrEnum):
    FREE = "free"
    CHOICE = "choice"
    FLOAT = "float"
    MAKE_OBSERVATION = "make_observation"
    NEXT_ACTING = "next_acting"
    NEXT_ACTION_SPEC = "next_action_spec"
    RESOLVE = "resolve"
    TERMINATE = "terminate"
    NEXT_GAME_MASTER = "next_game_master"
    SKIP_THIS_STEP = "skip_this_step"


class StoryActionKind(StrEnum):
    FREE_ACTION = "free_action"
    DIALOGUE = "dialogue"
    REACTION = "reaction"
    INTERNAL_DECISION = "internal_decision"
    CHOICE = "choice"
    WAIT = "wait"
    SCENE_PROPOSAL = "scene_proposal"


class ActionSpec(RuntimeModel):
    """Serializable project-side equivalent of Concordia's ActionSpec."""

    spec_id: Identifier
    output_type: ActionOutputType
    action_kind: StoryActionKind | None = None
    call_to_action: str = Field(min_length=1, max_length=32_768)
    options: tuple[str, ...] = ()
    option_ids: tuple[Identifier, ...] = ()
    tag: Identifier | None = None
    content_locale: LocaleCode
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        choice_like = {
            ActionOutputType.CHOICE,
            ActionOutputType.NEXT_ACTING,
            ActionOutputType.TERMINATE,
            ActionOutputType.NEXT_GAME_MASTER,
        }
        if self.output_type in choice_like:
            if not self.options:
                raise ValueError("choice-like ActionSpec requires options")
            if len(self.options) != len(set(self.options)):
                raise ValueError("ActionSpec options must be unique")
        elif self.options:
            raise ValueError("non-choice ActionSpec cannot contain options")
        if self.option_ids and len(self.option_ids) != len(self.options):
            raise ValueError("option_ids must align with options")
        if len(self.option_ids) != len(set(self.option_ids)):
            raise ValueError("ActionSpec option_ids must be unique")
        return self


class ActionSpecEnvelope(RuntimeModel):
    """Model-written subset of an ActionSpec; system fields are filled locally."""

    call_to_action: str = Field(min_length=1, max_length=32_768)
    output_type: ActionOutputType
    options: tuple[str, ...] = ()
    tag: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_options(self) -> Self:
        choice_like = {
            ActionOutputType.CHOICE,
            ActionOutputType.NEXT_ACTING,
            ActionOutputType.TERMINATE,
            ActionOutputType.NEXT_GAME_MASTER,
        }
        if self.output_type in choice_like:
            if not self.options:
                raise ValueError("choice-like ActionSpec requires options")
            if len(self.options) != len(set(self.options)):
                raise ValueError("ActionSpec options must be unique")
        elif self.options:
            raise ValueError("non-choice ActionSpec cannot contain options")
        return self


class TaskType(StrEnum):
    ACTOR = "actor"
    GAME_MASTER = "game_master"
    WIKI_MAINTENANCE = "wiki_maintenance"
    EDITOR = "editor"
    WRITER = "writer"


class TaskSpec(RuntimeModel):
    """Application-level model request metadata, distinct from ActionSpec."""

    task_id: Identifier
    task_type: TaskType
    profile_id: Identifier
    content_locale: LocaleCode
    prompt_version: Identifier
    instructions: str = Field(min_length=1, max_length=131_072)
    output_schema: dict[str, JsonValue] | None = None
    max_output_tokens: int = Field(default=2048, ge=1, le=65_536)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    temperature: float | None = Field(default=None, ge=0, le=2)
    actor_id: Identifier | None = None
    session_id: Identifier | None = None
    step: int | None = Field(default=None, ge=0)
    source_record_ids: tuple[Identifier, ...] = ()
    tags: tuple[Identifier, ...] = ()


class IntentMode(StrEnum):
    EXTERNAL_ACTION = "external_action"
    SPEECH = "speech"
    INTERNAL = "internal"
    WAIT = "wait"


class CharacterIntent(RuntimeModel):
    """Optional structured envelope around an actor's natural-language action."""

    intent_id: Identifier
    actor_id: Identifier
    session_id: Identifier
    step: int = Field(ge=0)
    mode: IntentMode
    action_text: str = Field(min_length=1, max_length=32_768)
    goal_text: str | None = Field(default=None, max_length=16_384)
    risk_text: str | None = Field(default=None, max_length=16_384)
    target_ids: tuple[Identifier, ...] = ()
    source_memory_ids: tuple[Identifier, ...] = ()
    content_locale: LocaleCode
    raw_action: str = Field(min_length=1, max_length=65_536)
