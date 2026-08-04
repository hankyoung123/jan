from typing import Annotated, Literal

from pydantic import BeforeValidator, Field, JsonValue

from story_engine.domain.models import DomainModel

ModelTask = Literal[
    "actor",
    "game_master",
    "wiki_maintenance",
    "editor",
    "writer",
]


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


class ModelProfile(DomainModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    task_type: ModelTask
    model_ref: str | None = Field(
        default=None,
        min_length=3,
        max_length=200,
        pattern=r"^[^/\s]+/.+$",
    )
    max_output_tokens: int = Field(default=2048, ge=1, le=8192)
    timeout_seconds: int = Field(default=60, ge=1, le=120)
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: ReasoningEffort | None = Field(default=None)


class ModelProfilePatch(DomainModel):
    model_ref: str | None = Field(
        default=None,
        min_length=3,
        max_length=200,
        pattern=r"^[^/\s]+/.+$",
    )
    max_output_tokens: int | None = Field(default=None, ge=1, le=8192)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120)
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
    max_output_tokens: int | None = Field(default=None, ge=1, le=8192)
    output_token_limit: Literal["profile", "provider"] = "profile"
    first_content_timeout_seconds: int | None = Field(
        default=None,
        ge=1,
        le=300,
    )
    timeout_seconds: int = Field(ge=1, le=120)
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
    max_tokens: int | None = Field(default=None, ge=1, le=8192)


class ModelStreamChunk(DomainModel):
    delta: str = ""
    done: bool = False
    usage: ModelUsage | None = None


class UsageTotals(DomainModel):
    requests: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ModelCatalog(DomainModel):
    profiles: tuple[ModelProfile, ...]
