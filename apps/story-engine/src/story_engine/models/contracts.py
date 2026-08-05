from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, JsonValue

from story_engine.domain.models import DomainModel
from story_engine.models.limits import (
    MAX_MODEL_OUTPUT_TOKENS,
    MAX_MODEL_TIMEOUT_SECONDS,
)

AgentType = Literal[
    "actor",
    "game_master",
    "writer",
    "editor",
    "wiki_maintainer",
    "submission_editor",
]
ModelTask = AgentType


def normalize_reasoning_effort(value: object) -> object:
    if value == "disabled":
        return "none"
    return value


ReasoningEffort = Annotated[
    Literal[
        "none",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ],
    BeforeValidator(normalize_reasoning_effort),
]
MessageRole = Literal["system", "user", "assistant"]


class AgentProfile(DomainModel):
    name: str = Field(min_length=1, max_length=100)
    agent_type: AgentType
    default_system_prompt: str = Field(min_length=1, max_length=65_536)
    model: str | None = Field(
        default=None,
        min_length=3,
        max_length=200,
        pattern=r"^[^/\s]+/.+$",
    )
    max_output_tokens: int | None = Field(
        default=2048,
        ge=1,
        le=MAX_MODEL_OUTPUT_TOKENS,
    )
    timeout_seconds: int = Field(
        default=60,
        ge=1,
        le=MAX_MODEL_TIMEOUT_SECONDS,
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = Field(default=None)


class AgentProfilePatch(DomainModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    default_system_prompt: str | None = Field(
        default=None,
        min_length=1,
        max_length=65_536,
    )
    model: str | None = Field(
        default=None,
        min_length=3,
        max_length=200,
        pattern=r"^[^/\s]+/.+$",
    )
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        le=MAX_MODEL_OUTPUT_TOKENS,
    )
    timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=MAX_MODEL_TIMEOUT_SECONDS,
    )
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = Field(default=None)


class Message(DomainModel):
    role: MessageRole
    content: str = Field(min_length=1, max_length=262_144)


class ModelRequest(DomainModel):
    profile_id: str = Field(min_length=1)
    task_type: ModelTask
    messages: tuple[Message, ...] = Field(min_length=1, max_length=128)
    output_schema: str | None = Field(default=None, max_length=131_072)
    max_output_tokens: int | None = Field(
        default=None,
        ge=1,
        le=MAX_MODEL_OUTPUT_TOKENS,
    )
    output_token_limit: Literal["profile", "provider"] = "profile"
    first_content_timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=300,
    )
    timeout_seconds: int = Field(ge=1, le=MAX_MODEL_TIMEOUT_SECONDS)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = Field(default=None)


class ModelUsage(DomainModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class ModelResponse(DomainModel):
    profile_id: str
    model_ref: str | None = None
    content: str
    parsed_output: JsonValue = None
    finish_reason: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)
    retry_count: int = Field(default=0, ge=0)
    max_tokens: int | None = Field(
        default=None,
        ge=1,
        le=MAX_MODEL_OUTPUT_TOKENS,
    )


class ModelStreamChunk(DomainModel):
    delta: str = ""
    done: bool = False
    usage: ModelUsage | None = None


class UsageTotals(DomainModel):
    requests: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class AgentProfileCatalog(DomainModel):
    profiles: tuple[AgentProfile, ...]
