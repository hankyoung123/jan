"""Gateway policy, structured output validation, and model message events."""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from threading import Lock
from typing import Any, Literal, cast

from jsonschema import SchemaError
from jsonschema.validators import validator_for
from pydantic import JsonValue

from story_engine.domain.message import (
    MessagePartDelta,
    ModelMessageContext,
    ModelMessageEvent,
    ModelMessageEventType,
    ModelMessagePart,
    ModelMessageSink,
    StoryMessageMetadata,
)
from story_engine.models import transport as _transport
from story_engine.models.contracts import (
    AgentProfile,
    Message,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
    ModelUsage,
    UsageTotals,
)
from story_engine.models.errors import (
    ModelConfigurationError,
    ModelTimeoutError,
    ProfileMismatchError,
    ProviderResponseError,
    ResponseLimitError,
    StructuredOutputError,
    UnsupportedResponseFormatError,
)
from story_engine.models.registry import ProfileRegistry
from story_engine.models.structured_output import (
    expanded_budget,
    prompt_only_payload,
    retry_payload,
    validate_output,
)
from story_engine.models.transport import (
    MAX_RESPONSE_BYTES,
    ModelPartSink,
    ModelTransport,
    usage_from_mapping,
)

OpenAICompatibleTransport = _transport.OpenAICompatibleTransport
UnavailableModelTransport = _transport.UnavailableModelTransport

__all__ = [
    "MAX_RESPONSE_BYTES",
    "ModelGateway",
    "ModelPartSink",
    "ModelTransport",
    "OpenAICompatibleTransport",
    "UnavailableModelTransport",
    "initial_budget",
]

MAX_REQUEST_BYTES = 1_048_576
TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens"})
MAX_STRUCTURED_ATTEMPTS = 2
MODEL_MESSAGE_FIRST_CONTENT_TIMEOUT_SECONDS = 300
MODEL_MESSAGE_DELTA_FLUSH_INTERVAL_SECONDS = 0.1
MODEL_MESSAGE_DELTA_FLUSH_CHARACTERS = 512
logger = logging.getLogger(__name__)


class _ModelMessageDeltaBatcher:
    def __init__(self, sink: ModelPartSink) -> None:
        self._sink = sink
        self._loop = asyncio.get_running_loop()
        self._timer: asyncio.TimerHandle | None = None
        self._pending: list[tuple[Literal["reasoning", "text"], str]] = []
        self._pending_characters = 0
        self._parts: list[ModelMessagePart] = []

    @staticmethod
    def _append_text(
        target: list[tuple[Literal["reasoning", "text"], str]],
        part_type: Literal["reasoning", "text"],
        text: str,
    ) -> None:
        if target and target[-1][0] == part_type:
            previous_type, previous_text = target[-1]
            target[-1] = (previous_type, previous_text + text)
        else:
            target.append((part_type, text))

    def add(self, part_type: Literal["reasoning", "text"], delta: str) -> None:
        if not delta:
            return
        self._append_text(self._pending, part_type, delta)
        if self._parts and self._parts[-1].type == part_type:
            previous = self._parts[-1]
            self._parts[-1] = previous.model_copy(
                update={"text": f"{previous.text or ''}{delta}"}
            )
        else:
            self._parts.append(ModelMessagePart(type=part_type, text=delta))
        self._pending_characters += len(delta)
        if self._pending_characters >= MODEL_MESSAGE_DELTA_FLUSH_CHARACTERS:
            self.flush()
        elif self._timer is None:
            self._timer = self._loop.call_later(
                MODEL_MESSAGE_DELTA_FLUSH_INTERVAL_SECONDS,
                self.flush,
            )

    def flush(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        pending = self._pending
        self._pending = []
        self._pending_characters = 0
        for part_type, text in pending:
            self._sink(part_type, text)

    def reset(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._pending = []
        self._pending_characters = 0
        self._parts = []

    @property
    def part_types(self) -> set[str]:
        return {part.type for part in self._parts}

    @property
    def parts(self) -> tuple[ModelMessagePart, ...]:
        return tuple(self._parts)


def initial_budget(task_kind: str, ceiling: int | None) -> int | None:
    """First-attempt budget for a call kind; the Profile remains the ceiling."""
    if ceiling is None:
        return None
    if task_kind == "choice":
        return min(1024, ceiling)
    if task_kind == "short_json":
        return min(2048, ceiling)
    return ceiling


def _reasoning_tokens(raw: Mapping[str, Any]) -> int | None:
    usage = raw.get("usage")
    if not isinstance(usage, Mapping):
        return None
    details = usage.get("completion_tokens_details")
    if not isinstance(details, Mapping):
        return None
    value = details.get("reasoning_tokens")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


class UsageTracker:
    def __init__(self) -> None:
        self._lock = Lock()
        self._totals = UsageTotals()

    def record(self, usage: ModelUsage) -> None:
        with self._lock:
            self._totals = UsageTotals(
                requests=self._totals.requests + 1,
                prompt_tokens=self._totals.prompt_tokens + usage.prompt_tokens,
                completion_tokens=(
                    self._totals.completion_tokens + usage.completion_tokens
                ),
                total_tokens=self._totals.total_tokens + usage.total_tokens,
            )

    def totals(self) -> UsageTotals:
        with self._lock:
            return self._totals


class ModelGateway:
    def __init__(
        self,
        registry: ProfileRegistry,
        transport: ModelTransport,
        usage: UsageTracker | None = None,
        message_sink: ModelMessageSink | None = None,
    ) -> None:
        self.registry = registry
        self.transport = transport
        self.usage = usage or UsageTracker()
        self.message_sink = message_sink

    @staticmethod
    def _message_metadata(
        *,
        message_id: str,
        context: ModelMessageContext,
        profile: AgentProfile,
        response: ModelResponse | None = None,
        duration_ms: int | None = None,
    ) -> StoryMessageMetadata:
        return StoryMessageMetadata(
            call_id=message_id,
            agent_type=profile.agent_type,
            agent_name=context.agent_name,
            task_label=context.task_label,
            session_id=context.session_id,
            branch_id=context.branch_id,
            step=context.step,
            stage=context.stage,
            stage_event_id=context.stage_event_id,
            model=response.model_ref if response else profile.model,
            duration_ms=duration_ms,
            prompt_tokens=response.usage.prompt_tokens if response else 0,
            completion_tokens=response.usage.completion_tokens if response else 0,
            reasoning_tokens=response.usage.reasoning_tokens if response else None,
            retry_count=response.retry_count if response else 0,
        )

    def _publish_message(
        self,
        *,
        event_type: ModelMessageEventType,
        message_id: str,
        context: ModelMessageContext,
        profile: AgentProfile,
        response: ModelResponse | None = None,
        duration_ms: int | None = None,
        part: MessagePartDelta | None = None,
        parts: tuple[ModelMessagePart, ...] = (),
        input_messages: tuple[Message, ...] = (),
        output_schema: str | None = None,
        error: str | None = None,
        reset: bool = False,
    ) -> None:
        if self.message_sink is None:
            return
        try:
            self.message_sink(
                ModelMessageEvent(
                    event_type=event_type,
                    project_id=context.project_id,
                    message_id=message_id,
                    metadata=self._message_metadata(
                        message_id=message_id,
                        context=context,
                        profile=profile,
                        response=response,
                        duration_ms=duration_ms,
                    ),
                    part=part,
                    parts=parts,
                    input_messages=input_messages,
                    output_schema=output_schema,
                    error=error,
                    reset=reset,
                )
            )
        except Exception:
            logger.exception(
                "failed to publish model message event",
                extra={"message_id": message_id, "event_type": event_type},
            )

    def _resolve(self, request: ModelRequest) -> AgentProfile:
        profile = self.registry.get_profile(request.profile_id)
        if profile.model is None:
            raise ModelConfigurationError(
                f"Agent {profile.agent_type!r} has no model selected"
            )
        if profile.agent_type != request.task_type:
            raise ProfileMismatchError(
                f"Agent {profile.agent_type!r} cannot run task {request.task_type!r}"
            )
        return profile

    @staticmethod
    def _schema(request: ModelRequest) -> Mapping[str, Any] | None:
        if request.output_schema is None:
            return None
        try:
            schema = json.loads(request.output_schema)
            if not isinstance(schema, dict):
                raise StructuredOutputError("output schema must be a JSON object")
            validator_type = validator_for(schema)
            validator_type.check_schema(schema)
            return cast(dict[str, Any], schema)
        except json.JSONDecodeError as error:
            raise StructuredOutputError("output schema is invalid JSON") from error
        except SchemaError as error:
            raise StructuredOutputError("output schema is invalid") from error

    @staticmethod
    def _payload(
        request: ModelRequest,
        profile: AgentProfile,
        schema: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        messages = [message.model_dump(mode="json") for message in request.messages]
        payload: dict[str, Any] = {"messages": messages}
        if request.output_token_limit == "profile":
            max_tokens = (
                request.max_output_tokens
                if request.max_output_tokens is not None
                else profile.max_output_tokens
            )
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
        if profile.model is not None:
            payload["model"] = profile.model
        temperature = (
            request.temperature
            if request.temperature is not None
            else profile.temperature
        )
        if temperature is not None:
            payload["temperature"] = temperature
        reasoning_effort = (
            request.reasoning_effort
            if request.reasoning_effort is not None
            else profile.reasoning_effort
        )
        if reasoning_effort is not None:
            payload["reasoning_effort"] = reasoning_effort
        if schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "story_engine_output", "schema": schema},
            }
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ResponseLimitError(
                f"model request exceeded {MAX_REQUEST_BYTES} bytes"
            )
        return payload

    @staticmethod
    def _prompt_only_structured_payload(
        payload: Mapping[str, Any],
        schema: Mapping[str, Any],
    ) -> dict[str, Any]:
        return prompt_only_payload(payload, schema)

    @staticmethod
    def _structured_retry_payload(
        payload: Mapping[str, Any],
        feedback: str,
    ) -> dict[str, Any]:
        return retry_payload(payload, feedback)

    @staticmethod
    def _parse_content(
        raw: Mapping[str, Any],
    ) -> tuple[str, str, str | None, ModelUsage]:
        choices = raw.get("choices")
        if (
            not isinstance(choices, Sequence)
            or isinstance(choices, (str, bytes))
            or not choices
            or not isinstance(choices[0], Mapping)
        ):
            raise ProviderResponseError("provider response has no completion choice")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, Mapping) or not isinstance(
            message.get("content"), str
        ):
            raise ProviderResponseError("provider response has no text content")
        finish_reason = choice.get("finish_reason")
        usage_value = raw.get("usage")
        usage = (
            usage_from_mapping(usage_value)
            if isinstance(usage_value, Mapping)
            else ModelUsage()
        )
        reasoning_content = message.get("reasoning_content")
        return (
            cast(str, message["content"]),
            reasoning_content if isinstance(reasoning_content, str) else "",
            finish_reason if isinstance(finish_reason, str) else None,
            usage,
        )

    @staticmethod
    def _validate_output(
        content: str,
        schema: Mapping[str, Any] | None,
    ) -> JsonValue:
        return validate_output(content, schema)

    @staticmethod
    def _expanded_budget(
        current_budget: int,
        *,
        reasoning_tokens: int | None,
        ceiling: int,
    ) -> int:
        return expanded_budget(
            current_budget,
            reasoning_tokens=reasoning_tokens,
            ceiling=ceiling,
        )

    async def _complete_request(
        self,
        request: ModelRequest,
        profile: AgentProfile,
        *,
        part_sink: ModelPartSink | None,
        retry_sink: Callable[[], None] | None,
    ) -> ModelResponse:
        schema = self._schema(request)
        payload = self._payload(request, profile, schema)
        can_fallback_to_prompt = schema is not None
        budget_ceiling = (
            max(
                profile.max_output_tokens or 0,
                request.max_output_tokens or 0,
            )
            if request.output_token_limit == "profile"
            and (profile.max_output_tokens or request.max_output_tokens)
            else None
        )

        async def transport_complete(
            current_payload: Mapping[str, Any],
        ) -> Mapping[str, Any]:
            if request.first_content_timeout_seconds is None:
                return await self.transport.complete(
                    current_payload,
                    timeout_seconds=request.timeout_seconds,
                )
            if part_sink is None:
                return await self.transport.complete(
                    current_payload,
                    timeout_seconds=request.timeout_seconds,
                    first_content_timeout_seconds=request.first_content_timeout_seconds,
                )
            streamed_part_sink = part_sink
            if schema is not None:
                def publish_structured_part(
                    part_type: Literal["reasoning", "text"],
                    delta: str,
                ) -> None:
                    if part_type == "reasoning":
                        part_sink(part_type, delta)

                streamed_part_sink = publish_structured_part
            return await self.transport.complete(
                current_payload,
                timeout_seconds=request.timeout_seconds,
                first_content_timeout_seconds=request.first_content_timeout_seconds,
                part_sink=streamed_part_sink,
            )

        total_timeout = request.timeout_seconds
        if request.first_content_timeout_seconds is not None:
            total_timeout += request.first_content_timeout_seconds
        max_attempts = (
            MAX_STRUCTURED_ATTEMPTS
            if request.structured_output_retry == "gateway"
            else 1
        )
        try:
            async with asyncio.timeout(total_timeout):
                for attempt in range(max_attempts):
                    try:
                        raw = await transport_complete(payload)
                    except UnsupportedResponseFormatError:
                        if not can_fallback_to_prompt or schema is None:
                            raise
                        payload = self._prompt_only_structured_payload(payload, schema)
                        can_fallback_to_prompt = False
                        if retry_sink is not None:
                            retry_sink()
                        raw = await transport_complete(payload)
                    content, reasoning_content, finish_reason, usage = (
                        self._parse_content(raw)
                    )
                    self.usage.record(usage)
                    if finish_reason in TRUNCATED_FINISH_REASONS:
                        message = (
                            "model JSON output was truncated at max_tokens"
                            if schema is not None
                            else "model output was truncated at max_tokens"
                        )
                        reasoning_tokens = _reasoning_tokens(raw)
                        if reasoning_tokens is not None:
                            message += (
                                f" (finish_reason={finish_reason}, "
                                f"reasoning_tokens={reasoning_tokens})"
                            )
                        else:
                            message += f" (finish_reason={finish_reason})"
                        current_budget = payload.get("max_tokens")
                        expanded: int | None = None
                        if budget_ceiling is not None:
                            if not isinstance(current_budget, int):
                                current_budget = request.max_output_tokens or (
                                    profile.max_output_tokens
                                )
                            if current_budget is not None:
                                expanded = self._expanded_budget(
                                    current_budget,
                                    reasoning_tokens=reasoning_tokens,
                                    ceiling=budget_ceiling,
                                )
                        if (
                            attempt == max_attempts - 1
                            or (
                                expanded is not None
                                and expanded <= (current_budget or 0)
                            )
                        ):
                            error = ResponseLimitError(message)
                            error.usage = usage
                            error.retry_count = attempt
                            error.finish_reason = finish_reason
                            error.max_tokens = current_budget
                            raise error
                        if expanded is not None:
                            payload["max_tokens"] = expanded
                        payload = self._structured_retry_payload(payload, message)
                        if retry_sink is not None:
                            retry_sink()
                        continue
                    if schema is None and not content.strip():
                        empty_error = StructuredOutputError(
                            "model returned empty text content",
                            retryable=True,
                        )
                        empty_error.usage = usage
                        empty_error.retry_count = attempt
                        if attempt == max_attempts - 1:
                            raise empty_error
                        payload = self._structured_retry_payload(
                            payload,
                            str(empty_error),
                        )
                        if retry_sink is not None:
                            retry_sink()
                        continue
                    try:
                        parsed_output = self._validate_output(content, schema)
                    except StructuredOutputError as error:
                        error.usage = usage
                        error.retry_count = attempt
                        if (
                            not error.retryable
                            or attempt == max_attempts - 1
                        ):
                            raise
                        payload = self._structured_retry_payload(payload, str(error))
                        if retry_sink is not None:
                            retry_sink()
                        continue
                    return ModelResponse(
                        profile_id=profile.agent_type,
                        model_ref=(
                            raw.get("model")
                            if isinstance(raw.get("model"), str)
                            else profile.model
                        ),
                        content=content,
                        reasoning_content=reasoning_content,
                        parsed_output=parsed_output,
                        finish_reason=finish_reason,
                        usage=usage,
                        retry_count=attempt,
                        max_tokens=payload.get("max_tokens"),
                    )
        except TimeoutError as error:
            raise ModelTimeoutError(
                f"model request for profile '{request.profile_id}' exceeded "
                f"its {total_timeout}s total deadline"
            ) from error
        raise ProviderResponseError("model request failed")

    async def complete(
        self,
        request: ModelRequest,
        *,
        context: ModelMessageContext | None = None,
    ) -> ModelResponse:
        profile = self._resolve(request)
        if context is None:
            return await self._complete_request(
                request,
                profile,
                part_sink=None,
                retry_sink=None,
            )

        if request.first_content_timeout_seconds is None:
            request = request.model_copy(
                update={
                    "first_content_timeout_seconds": (
                        MODEL_MESSAGE_FIRST_CONTENT_TIMEOUT_SECONDS
                    )
                }
            )

        message_id = context.message_id or f"call:{uuid.uuid4().hex}"
        started = time.monotonic()

        def duration_ms() -> int:
            return max(0, int((time.monotonic() - started) * 1000))

        def publish_delta(
            part_type: Literal["reasoning", "text"],
            delta: str,
        ) -> None:
            self._publish_message(
                event_type="model.message.delta",
                message_id=message_id,
                context=context,
                profile=profile,
                duration_ms=duration_ms(),
                part=MessagePartDelta(type=part_type, text_delta=delta),
            )

        batcher = _ModelMessageDeltaBatcher(publish_delta)

        def reset_attempt() -> None:
            batcher.reset()
            self._publish_message(
                event_type="model.message.started",
                message_id=message_id,
                context=context,
                profile=profile,
                duration_ms=duration_ms(),
                input_messages=request.messages,
                output_schema=request.output_schema,
                reset=True,
            )

        self._publish_message(
            event_type="model.message.started",
            message_id=message_id,
            context=context,
            profile=profile,
            input_messages=request.messages,
            output_schema=request.output_schema,
            reset=True,
        )
        try:
            response = await self._complete_request(
                request,
                profile,
                part_sink=batcher.add,
                retry_sink=reset_attempt,
            )
        except Exception as error:
            batcher.flush()
            self._publish_message(
                event_type="model.message.failed",
                message_id=message_id,
                context=context,
                profile=profile,
                duration_ms=duration_ms(),
                parts=batcher.parts,
                error=str(error),
            )
            raise

        if response.reasoning_content and "reasoning" not in batcher.part_types:
            batcher.add("reasoning", response.reasoning_content)
        if response.content and "text" not in batcher.part_types:
            batcher.add("text", response.content)
        batcher.flush()
        self._publish_message(
            event_type="model.message.completed",
            message_id=message_id,
            context=context,
            profile=profile,
            response=response,
            duration_ms=duration_ms(),
            parts=batcher.parts,
        )
        return response

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamChunk]:
        profile = self._resolve(request)
        schema = self._schema(request)
        payload = self._payload(request, profile, schema)
        content: list[str] = []
        usage = ModelUsage()
        size = 0
        try:
            async with asyncio.timeout(request.timeout_seconds):
                async for chunk in self.transport.stream(
                    payload,
                    timeout_seconds=request.timeout_seconds,
                ):
                    if chunk.delta:
                        size += len(chunk.delta.encode("utf-8"))
                        if size > MAX_RESPONSE_BYTES:
                            raise ResponseLimitError(
                                f"model output exceeded {MAX_RESPONSE_BYTES} bytes"
                            )
                        content.append(chunk.delta)
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if chunk.done:
                        self._validate_output("".join(content), schema)
                        self.usage.record(usage)
                    yield chunk
        except TimeoutError as error:
            raise ModelTimeoutError(
                "model stream exceeded its total deadline"
            ) from error
