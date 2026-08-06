import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any, Literal, Protocol, cast

import httpx
from jsonschema import SchemaError, ValidationError
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
from story_engine.models.contracts import (
    AgentProfile,
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

MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 1_048_576
MAX_SSE_TRANSPORT_BYTES = 16 * MAX_RESPONSE_BYTES
MAX_ATTEMPTS = 3
MAX_PROVIDER_ERROR_DETAIL_CHARS = 1_000
TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens"})
MAX_STRUCTURED_ATTEMPTS = 3
STRUCTURED_RETRY_BACKOFF_SECONDS = 0.5
MODEL_MESSAGE_FIRST_CONTENT_TIMEOUT_SECONDS = 300
MODEL_MESSAGE_DELTA_FLUSH_INTERVAL_SECONDS = 0.1
MODEL_MESSAGE_DELTA_FLUSH_CHARACTERS = 512
logger = logging.getLogger(__name__)

ModelPartSink = Callable[[Literal["reasoning", "text"], str], None]


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


class ModelTransport(Protocol):
    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        first_content_timeout_seconds: float | None = None,
        part_sink: ModelPartSink | None = None,
    ) -> Mapping[str, Any]: ...

    def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[ModelStreamChunk]: ...


class _TransientProviderError(Exception):
    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def _headers(api_key: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def _endpoint(base_url: str, resource: str) -> str:
    return f"{base_url.rstrip('/')}/{resource.lstrip('/')}"


async def _read_limited(response: httpx.Response) -> bytes:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > MAX_RESPONSE_BYTES:
            raise ResponseLimitError(
                f"provider response exceeded {MAX_RESPONSE_BYTES} bytes"
            )
    return bytes(content)


def _provider_error_detail(content: bytes) -> str | None:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None

    candidates: list[Any] = [payload.get("message"), payload.get("detail")]
    error = payload.get("error")
    if isinstance(error, Mapping):
        candidates.extend((error.get("message"), error.get("detail")))
    elif isinstance(error, str):
        candidates.append(error)

    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        detail = " ".join(candidate.split())
        if detail:
            return detail[:MAX_PROVIDER_ERROR_DETAIL_CHARS]
    return None


async def _raise_for_status(response: httpx.Response) -> None:
    if not response.is_error:
        return
    content = await _read_limited(response)
    detail = _provider_error_detail(content)
    message = f"provider returned HTTP {response.status_code}"
    if detail is not None:
        message = f"{message}: {detail}"
    if response.status_code == 400 and _response_format_is_unavailable(detail):
        raise UnsupportedResponseFormatError(message)
    if response.status_code == 429 or response.status_code >= 500:
        raise _TransientProviderError(
            message,
            retry_after=_retry_after_seconds(response),
        )
    raise ProviderResponseError(message)


def _response_format_is_unavailable(detail: str | None) -> bool:
    if detail is None:
        return False
    normalized = detail.casefold().replace("-", "_")
    names_format = "response_format" in normalized or "response format" in normalized
    unavailable = any(
        marker in normalized
        for marker in (
            "unavailable",
            "unsupported",
            "not supported",
            "invalid type",
            "unknown type",
        )
    )
    return names_format and unavailable


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


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())


class OpenAICompatibleTransport:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url
        self._api_key = api_key
        self._transport = transport

    def _client(self, timeout_seconds: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=self._transport,
        )

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        first_content_timeout_seconds: float | None = None,
        part_sink: ModelPartSink | None = None,
    ) -> Mapping[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        if first_content_timeout_seconds is not None:
            deadline += first_content_timeout_seconds
        emitted_part = False

        def publish_part(part_type: Literal["reasoning", "text"], delta: str) -> None:
            nonlocal emitted_part
            emitted_part = True
            if part_sink is not None:
                part_sink(part_type, delta)

        for attempt in range(MAX_ATTEMPTS):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelTimeoutError("model request exceeded its total deadline")
            retry_error: httpx.NetworkError | _TransientProviderError | None = None
            try:
                if first_content_timeout_seconds is not None:
                    return await self._complete_streamed(
                        payload,
                        timeout_seconds=timeout_seconds,
                        first_content_timeout_seconds=min(
                            first_content_timeout_seconds,
                            remaining,
                        ),
                        part_sink=publish_part,
                    )
                async with (
                    self._client(remaining) as client,
                    asyncio.timeout(remaining),
                    client.stream(
                        "POST",
                        _endpoint(self._base_url, "chat/completions"),
                        headers=_headers(self._api_key),
                        json=payload,
                    ) as response,
                ):
                    await _raise_for_status(response)
                    content = await _read_limited(response)
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ProviderResponseError("provider response must be an object")
                return cast(dict[str, Any], parsed)
            except (TimeoutError, httpx.TimeoutException) as error:
                if first_content_timeout_seconds is not None:
                    raise ModelTimeoutError(
                        "model request produced no content token within "
                        f"{first_content_timeout_seconds:g}s"
                    ) from error
                raise ModelTimeoutError("model provider request timed out") from error
            except (httpx.NetworkError, _TransientProviderError) as error:
                if emitted_part:
                    raise ProviderResponseError(
                        "provider stream was interrupted after output started"
                    ) from error
                if attempt == MAX_ATTEMPTS - 1:
                    if isinstance(error, _TransientProviderError):
                        raise ProviderResponseError(str(error)) from error
                    raise ProviderResponseError(
                        "provider was unavailable after retries"
                    ) from error
                retry_error = error
            except json.JSONDecodeError as error:
                raise ProviderResponseError("provider returned invalid JSON") from error
            retry_after = (
                retry_error.retry_after
                if isinstance(retry_error, _TransientProviderError)
                else None
            )
            delay = retry_after if retry_after is not None else 0.1 * (2**attempt)
            remaining = deadline - time.monotonic()
            if delay >= remaining:
                raise ModelTimeoutError(
                    "model retry delay exceeded its total deadline"
                ) from retry_error
            await asyncio.sleep(delay)
        raise ProviderResponseError("provider request failed")

    async def _complete_streamed(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        first_content_timeout_seconds: float,
        part_sink: ModelPartSink | None = None,
    ) -> Mapping[str, Any]:
        streamed_payload = {
            **payload,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        started_at = time.monotonic()
        first_content_deadline = started_at + first_content_timeout_seconds
        completion_deadline: float | None = None
        content: list[str] = []
        reasoning_content: list[str] = []
        finish_reason: str | None = None
        model: str | None = None
        usage: Mapping[str, Any] = {}
        transport_bytes = 0
        content_bytes = 0
        client_timeout = max(timeout_seconds, first_content_timeout_seconds)

        async with (
            self._client(client_timeout) as client,
            client.stream(
                "POST",
                _endpoint(self._base_url, "chat/completions"),
                headers=_headers(self._api_key),
                json=streamed_payload,
            ) as response,
        ):
            await _raise_for_status(response)
            lines = response.aiter_lines()
            while True:
                deadline = completion_deadline or first_content_deadline
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._raise_content_timeout(
                        content_started=completion_deadline is not None,
                        timeout_seconds=(
                            timeout_seconds
                            if completion_deadline is not None
                            else first_content_timeout_seconds
                        ),
                    )
                try:
                    async with asyncio.timeout(remaining):
                        line = await anext(lines)
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    self._raise_content_timeout(
                        content_started=completion_deadline is not None,
                        timeout_seconds=(
                            timeout_seconds
                            if completion_deadline is not None
                            else first_content_timeout_seconds
                        ),
                    )

                transport_bytes += len(line.encode("utf-8")) + 1
                if transport_bytes > MAX_SSE_TRANSPORT_BYTES:
                    raise ResponseLimitError(
                        "provider SSE transport exceeded "
                        f"{MAX_SSE_TRANSPORT_BYTES} bytes"
                    )
                if not line.startswith("data:"):
                    continue
                data = line.removeprefix("data:").strip()
                if data == "[DONE]":
                    break
                try:
                    event = json.loads(data)
                except json.JSONDecodeError as error:
                    raise ProviderResponseError(
                        "provider returned an invalid stream event"
                    ) from error
                if not isinstance(event, Mapping):
                    continue
                event_model = event.get("model")
                if isinstance(event_model, str):
                    model = event_model
                event_usage = event.get("usage")
                if isinstance(event_usage, Mapping):
                    usage = event_usage
                choices = event.get("choices")
                if (
                    isinstance(choices, Sequence)
                    and not isinstance(choices, (str, bytes))
                    and choices
                    and isinstance(choices[0], Mapping)
                ):
                    event_finish_reason = choices[0].get("finish_reason")
                    if isinstance(event_finish_reason, str):
                        finish_reason = event_finish_reason
                reasoning_delta, text_delta = _stream_part_delta(event)
                if reasoning_delta:
                    reasoning_content.append(reasoning_delta)
                    if part_sink is not None:
                        part_sink("reasoning", reasoning_delta)
                if text_delta:
                    content_bytes += len(text_delta.encode("utf-8"))
                    if content_bytes > MAX_RESPONSE_BYTES:
                        raise ResponseLimitError(
                            f"model output exceeded {MAX_RESPONSE_BYTES} bytes"
                        )
                    if completion_deadline is None:
                        completion_deadline = time.monotonic() + timeout_seconds
                    content.append(text_delta)
                    if part_sink is not None:
                        part_sink("text", text_delta)

        return {
            "model": model,
            "choices": [
                {
                    "message": {
                        "content": "".join(content),
                        "reasoning_content": "".join(reasoning_content),
                    },
                    "finish_reason": finish_reason,
                }
            ],
            "usage": usage,
        }

    @staticmethod
    def _raise_content_timeout(
        *,
        content_started: bool,
        timeout_seconds: float,
    ) -> None:
        if content_started:
            raise ModelTimeoutError(
                "model response did not finish within "
                f"{timeout_seconds:g}s after content started"
            )
        raise ModelTimeoutError(
            f"model request produced no content token within {timeout_seconds:g}s"
        )

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[ModelStreamChunk]:
        streamed_payload = {
            **payload,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        try:
            async with (
                self._client(timeout_seconds) as client,
                client.stream(
                    "POST",
                    _endpoint(self._base_url, "chat/completions"),
                    headers=_headers(self._api_key),
                    json=streamed_payload,
                ) as response,
            ):
                await _raise_for_status(response)
                received = 0
                usage: ModelUsage | None = None
                async for line in response.aiter_lines():
                    received += len(line.encode("utf-8")) + 1
                    if received > MAX_RESPONSE_BYTES:
                        raise ResponseLimitError(
                            f"provider response exceeded {MAX_RESPONSE_BYTES} bytes"
                        )
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        yield ModelStreamChunk(done=True, usage=usage)
                        return
                    try:
                        event = json.loads(data)
                        if not isinstance(event, dict):
                            continue
                        event_usage = event.get("usage")
                        if isinstance(event_usage, dict):
                            usage = _usage_from_mapping(event_usage)
                        delta = _stream_delta(event)
                    except (json.JSONDecodeError, TypeError, ValueError) as error:
                        raise ProviderResponseError(
                            "provider returned an invalid stream event"
                        ) from error
                    if delta:
                        yield ModelStreamChunk(delta=delta)
                yield ModelStreamChunk(done=True, usage=usage)
        except (TimeoutError, httpx.TimeoutException) as error:
            raise ModelTimeoutError("model stream timed out") from error
        except httpx.NetworkError as error:
            raise ProviderResponseError("provider stream was unavailable") from error
        except _TransientProviderError as error:
            raise ProviderResponseError("provider stream was unavailable") from error


class UnavailableModelTransport:
    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
        first_content_timeout_seconds: float | None = None,
        part_sink: ModelPartSink | None = None,
    ) -> Mapping[str, Any]:
        del payload, timeout_seconds, first_content_timeout_seconds, part_sink
        raise ModelConfigurationError("Story Engine model runtime is unavailable")

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[ModelStreamChunk]:
        if False:
            yield ModelStreamChunk()
        raise ModelConfigurationError("Story Engine model runtime is unavailable")


def _stream_delta(event: Mapping[str, Any]) -> str:
    return _stream_part_delta(event)[1]


def _stream_part_delta(event: Mapping[str, Any]) -> tuple[str, str]:
    choices = event.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
        return "", ""
    if not choices or not isinstance(choices[0], Mapping):
        return "", ""
    delta = choices[0].get("delta")
    if not isinstance(delta, Mapping):
        return "", ""
    reasoning = delta.get("reasoning_content")
    content = delta.get("content")
    return (
        reasoning if isinstance(reasoning, str) else "",
        content if isinstance(content, str) else "",
    )


def _usage_from_mapping(value: Mapping[str, Any]) -> ModelUsage:
    prompt = value.get("prompt_tokens", 0)
    completion = value.get("completion_tokens", 0)
    total = value.get("total_tokens", 0)
    reasoning: int | None = None
    details = value.get("completion_tokens_details")
    if isinstance(details, Mapping):
        detail_value = details.get("reasoning_tokens")
        if (
            isinstance(detail_value, int)
            and not isinstance(detail_value, bool)
            and detail_value >= 0
        ):
            reasoning = detail_value
    if reasoning is None:
        top_level = value.get("reasoning_tokens")
        if (
            isinstance(top_level, int)
            and not isinstance(top_level, bool)
            and top_level >= 0
        ):
            reasoning = top_level
    return ModelUsage(
        prompt_tokens=prompt if isinstance(prompt, int) else 0,
        completion_tokens=completion if isinstance(completion, int) else 0,
        total_tokens=total if isinstance(total, int) else 0,
        reasoning_tokens=reasoning,
    )


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
        payload: dict[str, Any] = {
            "messages": messages,
        }
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
        fallback = dict(payload)
        fallback.pop("response_format", None)
        raw_messages = fallback.get("messages")
        messages = list(raw_messages) if isinstance(raw_messages, list) else []
        instruction = {
            "role": "system",
            "content": (
                "Return only valid JSON matching this JSON Schema. Do not use "
                "Markdown fences or add explanatory text. JSON SCHEMA: "
                f"{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}"
            ),
        }
        insert_at = 0
        while (
            insert_at < len(messages)
            and isinstance(messages[insert_at], Mapping)
            and messages[insert_at].get("role") == "system"
        ):
            insert_at += 1
        messages.insert(insert_at, instruction)
        fallback["messages"] = messages
        encoded = json.dumps(fallback, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ResponseLimitError(
                f"model request exceeded {MAX_REQUEST_BYTES} bytes"
            )
        return fallback

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
            _usage_from_mapping(usage_value)
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
        if schema is None:
            return None
        if not content.strip():
            raise StructuredOutputError(
                "model returned empty JSON content",
                retryable=True,
            )
        try:
            parsed: JsonValue = json.loads(content)
        except json.JSONDecodeError as error:
            raise StructuredOutputError(
                "model output is not valid JSON",
                retryable=True,
            ) from error
        try:
            validator_for(schema)(schema).validate(parsed)
        except ValidationError as error:
            path = ".".join(str(item) for item in error.absolute_path)
            location = f" at {path}" if path else ""
            raise StructuredOutputError(
                f"model output failed schema validation{location}",
                retryable=True,
            ) from error
        return parsed

    @staticmethod
    def _expanded_budget(
        current_budget: int,
        *,
        reasoning_tokens: int | None,
        ceiling: int,
    ) -> int:
        next_budget = max(
            current_budget * 2,
            (reasoning_tokens if reasoning_tokens is not None else current_budget)
            + 128,
        )
        return min(ceiling, next_budget)

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
                    first_content_timeout_seconds=(
                        request.first_content_timeout_seconds
                    ),
                )
            streamed_part_sink = part_sink
            if schema is not None:
                # Structured text is held until schema validation succeeds. Reasoning
                # is presentation-only and can be streamed without exposing invalid
                # business output.
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
                first_content_timeout_seconds=(request.first_content_timeout_seconds),
                part_sink=streamed_part_sink,
            )

        total_timeout = request.timeout_seconds
        if request.first_content_timeout_seconds is not None:
            total_timeout += request.first_content_timeout_seconds
        try:
            async with asyncio.timeout(total_timeout):
                for attempt in range(MAX_STRUCTURED_ATTEMPTS):
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
                        if budget_ceiling is None:
                            error = ResponseLimitError(
                                f"{message}; the provider reached its own output limit"
                            )
                            error.usage = usage
                            error.retry_count = attempt
                            error.finish_reason = finish_reason
                            raise error
                        if not isinstance(current_budget, int):
                            current_budget = request.max_output_tokens or (
                                profile.max_output_tokens
                            )
                        if current_budget is None:
                            raise ResponseLimitError(message)
                        expanded = self._expanded_budget(
                            current_budget,
                            reasoning_tokens=reasoning_tokens,
                            ceiling=budget_ceiling,
                        )
                        if (
                            attempt == MAX_STRUCTURED_ATTEMPTS - 1
                            or expanded <= current_budget
                        ):
                            error = ResponseLimitError(message)
                            error.usage = usage
                            error.retry_count = attempt
                            error.finish_reason = finish_reason
                            error.max_tokens = current_budget
                            raise error
                        payload["max_tokens"] = expanded
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
                        if attempt == MAX_STRUCTURED_ATTEMPTS - 1:
                            raise empty_error
                        if retry_sink is not None:
                            retry_sink()
                        await asyncio.sleep(
                            STRUCTURED_RETRY_BACKOFF_SECONDS * (2**attempt)
                        )
                        continue
                    try:
                        parsed_output = self._validate_output(content, schema)
                    except StructuredOutputError as error:
                        error.usage = usage
                        error.retry_count = attempt
                        if (
                            not error.retryable
                            or attempt == MAX_STRUCTURED_ATTEMPTS - 1
                        ):
                            raise
                        if retry_sink is not None:
                            retry_sink()
                        await asyncio.sleep(
                            STRUCTURED_RETRY_BACKOFF_SECONDS * (2**attempt)
                        )
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
                reset=True,
            )

        self._publish_message(
            event_type="model.message.started",
            message_id=message_id,
            context=context,
            profile=profile,
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
