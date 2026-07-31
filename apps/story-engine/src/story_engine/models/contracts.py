from typing import Literal, Self
from urllib.parse import urlparse

from pydantic import Field, JsonValue, model_validator

from story_engine.domain.models import DomainModel

ModelTask = Literal["character", "resolver", "editor", "writer", "embedding"]
ProviderKind = Literal["remote", "local"]
MessageRole = Literal["system", "user", "assistant"]


class ProviderConfig(DomainModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=80)
    kind: ProviderKind
    base_url: str = Field(min_length=1, max_length=2048)
    requires_api_key: bool = False

    @model_validator(mode="after")
    def endpoint_matches_provider_kind(self) -> Self:
        parsed = urlparse(self.base_url)
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(
                "provider base_url must be an absolute URL without credentials"
            )
        if parsed.query or parsed.fragment:
            raise ValueError("provider base_url must not contain a query or fragment")
        if self.kind == "remote" and parsed.scheme != "https":
            raise ValueError("remote provider base_url must use HTTPS")
        if self.kind == "local" and (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        ):
            raise ValueError("local provider base_url must use a loopback endpoint")
        return self


class ModelProfile(DomainModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=80)
    task_type: ModelTask
    provider_id: str = Field(min_length=1)
    model: str = Field(min_length=1, max_length=200)
    max_output_tokens: int = Field(default=2048, ge=1, le=8192)
    timeout_seconds: int = Field(default=60, ge=1, le=120)
    temperature: float | None = Field(default=None, ge=0, le=2)
    enabled: bool = True


class Message(DomainModel):
    role: MessageRole
    content: str = Field(min_length=1, max_length=262_144)


class ModelRequest(DomainModel):
    profile_id: str = Field(min_length=1)
    task_type: ModelTask
    messages: tuple[Message, ...] = Field(min_length=1, max_length=128)
    output_schema: str | None = Field(default=None, max_length=131_072)
    max_output_tokens: int = Field(ge=1, le=8192)
    timeout_seconds: int = Field(ge=1, le=120)
    temperature: float | None = Field(default=None, ge=0, le=2)


class ModelUsage(DomainModel):
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ModelResponse(DomainModel):
    profile_id: str
    provider_id: str
    model: str
    content: str
    parsed_output: JsonValue = None
    finish_reason: str | None = None
    usage: ModelUsage = Field(default_factory=ModelUsage)


class ModelStreamChunk(DomainModel):
    delta: str = ""
    done: bool = False
    usage: ModelUsage | None = None


class UsageTotals(DomainModel):
    requests: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)


class ProviderView(DomainModel):
    id: str
    name: str
    kind: ProviderKind
    base_url: str
    requires_api_key: bool
    has_api_key: bool


class ModelCatalog(DomainModel):
    providers: tuple[ProviderView, ...]
    profiles: tuple[ModelProfile, ...]
