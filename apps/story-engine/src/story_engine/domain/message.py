from typing import Literal, Protocol, Self

from pydantic import ConfigDict, Field, JsonValue, model_validator

from story_engine.domain.base import Identifier, RuntimeModel
from story_engine.models.contracts import AgentType

ModelMessageEventType = Literal[
    "model.message.started",
    "model.message.delta",
    "model.message.completed",
    "model.message.failed",
]


class ModelMessageContext(RuntimeModel):
    project_id: Identifier
    message_id: Identifier | None = None
    agent_name: str = Field(min_length=1, max_length=200)
    task_label: str = Field(min_length=1, max_length=200)
    session_id: Identifier | None = None
    branch_id: Identifier | None = None
    step: int | None = Field(default=None, ge=0)
    stage: str | None = Field(default=None, max_length=100)


class StoryMessageMetadata(RuntimeModel):
    call_id: Identifier
    agent_type: AgentType
    agent_name: str = Field(min_length=1, max_length=200)
    task_label: str = Field(min_length=1, max_length=200)
    session_id: Identifier | None = None
    branch_id: Identifier | None = None
    step: int | None = Field(default=None, ge=0)
    stage: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=200)
    duration_ms: int | None = Field(default=None, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)


class MessagePartDelta(RuntimeModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    type: str = Field(pattern=r"^(reasoning|text|file|tool-[a-zA-Z0-9_-]+)$")
    text_delta: str | None = None
    state: str | None = Field(default=None, max_length=100)
    tool_call_id: str | None = Field(default=None, max_length=200)
    media_type: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=1_048_576)
    filename: str | None = Field(default=None, max_length=255)
    input: JsonValue = None
    output: JsonValue = None
    error: str | None = Field(default=None, max_length=8_000)

    @model_validator(mode="after")
    def content_matches_part_type(self) -> Self:
        if self.type in {"reasoning", "text"} and self.text_delta is None:
            raise ValueError("text and reasoning deltas require text_delta")
        if self.type.startswith("tool-") and self.state is None:
            raise ValueError("tool deltas require state")
        if self.type == "file" and (self.media_type is None or self.url is None):
            raise ValueError("file deltas require media_type and url")
        return self


class ModelMessagePart(RuntimeModel):
    model_config = ConfigDict(str_strip_whitespace=False)

    type: str = Field(pattern=r"^(reasoning|text|file|tool-[a-zA-Z0-9_-]+)$")
    text: str | None = None
    state: str | None = Field(default=None, max_length=100)
    tool_call_id: str | None = Field(default=None, max_length=200)
    media_type: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=1_048_576)
    filename: str | None = Field(default=None, max_length=255)
    input: JsonValue = None
    output: JsonValue = None
    error: str | None = Field(default=None, max_length=8_000)

    @model_validator(mode="after")
    def content_matches_part_type(self) -> Self:
        if self.type in {"reasoning", "text"} and self.text is None:
            raise ValueError("text and reasoning parts require text")
        if self.type.startswith("tool-") and self.state is None:
            raise ValueError("tool parts require state")
        if self.type == "file" and (self.media_type is None or self.url is None):
            raise ValueError("file parts require media_type and url")
        return self


class ModelMessageEvent(RuntimeModel):
    event_type: ModelMessageEventType
    project_id: Identifier
    message_id: Identifier
    role: Literal["assistant"] = "assistant"
    metadata: StoryMessageMetadata
    part: MessagePartDelta | None = None
    error: str | None = Field(default=None, max_length=8_000)
    reset: bool = False

    @model_validator(mode="after")
    def payload_matches_event_type(self) -> Self:
        if self.event_type == "model.message.delta" and self.part is None:
            raise ValueError("model.message.delta requires a part")
        if self.event_type != "model.message.delta" and self.part is not None:
            raise ValueError("only model.message.delta can contain a part")
        if self.event_type == "model.message.failed" and not self.error:
            raise ValueError("model.message.failed requires an error")
        return self


class ModelMessageSink(Protocol):
    def __call__(self, event: ModelMessageEvent) -> None: ...
