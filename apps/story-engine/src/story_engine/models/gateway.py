import asyncio
import json
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from threading import Lock
from typing import Any, Protocol, cast

import httpx
from jsonschema import SchemaError, ValidationError
from jsonschema.validators import validator_for
from pydantic import JsonValue

from story_engine.models.contracts import (
    ModelProfile,
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
)
from story_engine.models.registry import ProfileRegistry

MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 1_048_576
MAX_ATTEMPTS = 3
MAX_PROVIDER_ERROR_DETAIL_CHARS = 1_000
JSON_OBJECT_PROVIDERS = frozenset({"deepseek"})
TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens"})
MAX_STRUCTURED_ATTEMPTS = 3
STRUCTURED_RETRY_BACKOFF_SECONDS = 0.5
DEEPSEEK_THINKING_MIN_OUTPUT_TOKENS = 16384


class ModelTransport(Protocol):
    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...

    def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[ModelStreamChunk]: ...

    def embed(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]: ...


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
    if response.status_code == 429 or response.status_code >= 500:
        raise _TransientProviderError(
            message,
            retry_after=_retry_after_seconds(response),
        )
    raise ProviderResponseError(message)


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
    ) -> Mapping[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        for attempt in range(MAX_ATTEMPTS):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ModelTimeoutError("model request exceeded its total deadline")
            retry_error: httpx.NetworkError | _TransientProviderError | None = None
            try:
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
                raise ModelTimeoutError("model provider request timed out") from error
            except (httpx.NetworkError, _TransientProviderError) as error:
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

    def embed(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        """Call the OpenAI-compatible embedding endpoint from runtime threads."""
        try:
            with httpx.Client(
                timeout=httpx.Timeout(timeout_seconds),
                follow_redirects=False,
                transport=cast(httpx.BaseTransport | None, self._transport),
            ) as client:
                response = client.post(
                    _endpoint(self._base_url, "embeddings"),
                    headers=_headers(self._api_key),
                    json=payload,
                )
        except httpx.TimeoutException as error:
            raise ModelTimeoutError("embedding provider request timed out") from error
        except httpx.NetworkError as error:
            raise ProviderResponseError("embedding provider was unavailable") from error
        if response.is_error:
            detail = _provider_error_detail(response.content[:MAX_RESPONSE_BYTES])
            message = f"provider returned HTTP {response.status_code}"
            if detail:
                message = f"{message}: {detail}"
            raise ProviderResponseError(message)
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ResponseLimitError(
                f"provider response exceeded {MAX_RESPONSE_BYTES} bytes"
            )
        try:
            parsed = response.json()
        except json.JSONDecodeError as error:
            raise ProviderResponseError("provider returned invalid JSON") from error
        if not isinstance(parsed, dict):
            raise ProviderResponseError("embedding response must be an object")
        return cast(dict[str, Any], parsed)


class UnavailableModelTransport:
    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
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

    def embed(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del payload, timeout_seconds
        raise ModelConfigurationError("Story Engine embedding runtime is unavailable")


def _stream_delta(event: Mapping[str, Any]) -> str:
    choices = event.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
        return ""
    if not choices or not isinstance(choices[0], Mapping):
        return ""
    delta = choices[0].get("delta")
    if not isinstance(delta, Mapping):
        return ""
    content = delta.get("content")
    return content if isinstance(content, str) else ""


def _usage_from_mapping(value: Mapping[str, Any]) -> ModelUsage:
    prompt = value.get("prompt_tokens", 0)
    completion = value.get("completion_tokens", 0)
    total = value.get("total_tokens", 0)
    return ModelUsage(
        prompt_tokens=prompt if isinstance(prompt, int) else 0,
        completion_tokens=completion if isinstance(completion, int) else 0,
        total_tokens=total if isinstance(total, int) else 0,
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
    ) -> None:
        self.registry = registry
        self.transport = transport
        self.usage = usage or UsageTracker()

    def _resolve(self, request: ModelRequest) -> ModelProfile:
        profile = self.registry.get_profile(request.profile_id)
        if not profile.enabled:
            raise ModelConfigurationError(f"profile {profile.id!r} is disabled")
        if profile.task_type != request.task_type:
            raise ProfileMismatchError(
                f"profile {profile.id!r} is for {profile.task_type}, "
                f"not {request.task_type}"
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
        profile: ModelProfile,
        schema: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        messages = [message.model_dump(mode="json") for message in request.messages]
        uses_json_object = (
            schema is not None and profile.provider_id in JSON_OBJECT_PROVIDERS
        )
        if uses_json_object:
            schema_text = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": (
                        "Return exactly one valid JSON object as a single compact "
                        "line with no line breaks. The JSON object must match this "
                        "JSON Schema exactly; do not add Markdown fences, formatting "
                        "whitespace, or prose: "
                        f"{schema_text}"
                    ),
                },
            )
        thinking_enabled = (
            profile.provider_id == "deepseek" and profile.reasoning_effort != "disabled"
        )
        max_output_tokens = request.max_output_tokens
        if thinking_enabled:
            max_output_tokens = max(
                max_output_tokens,
                DEEPSEEK_THINKING_MIN_OUTPUT_TOKENS,
            )
        payload: dict[str, Any] = {
            "model": profile.model,
            "messages": messages,
            "max_tokens": max_output_tokens,
        }
        temperature = (
            request.temperature
            if request.temperature is not None
            else profile.temperature
        )
        if temperature is not None and not thinking_enabled:
            payload["temperature"] = temperature
        if profile.provider_id == "deepseek":
            if profile.reasoning_effort == "disabled":
                payload["thinking"] = {"type": "disabled"}
            else:
                payload["reasoning_effort"] = profile.reasoning_effort
                payload["thinking"] = {"type": "enabled"}
        if schema is not None:
            if uses_json_object:
                payload["response_format"] = {"type": "json_object"}
            else:
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
    def _parse_content(
        raw: Mapping[str, Any],
    ) -> tuple[str, str | None, ModelUsage]:
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
        return (
            cast(str, message["content"]),
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
                f"model output failed schema validation{location}"
            ) from error
        return parsed

    async def complete(self, request: ModelRequest) -> ModelResponse:
        profile = self._resolve(request)
        schema = self._schema(request)
        payload = self._payload(request, profile, schema)
        try:
            async with asyncio.timeout(request.timeout_seconds):
                for attempt in range(MAX_STRUCTURED_ATTEMPTS):
                    raw = await self.transport.complete(
                        payload,
                        timeout_seconds=request.timeout_seconds,
                    )
                    content, finish_reason, usage = self._parse_content(raw)
                    if schema is not None and finish_reason in TRUNCATED_FINISH_REASONS:
                        message = "model JSON output was truncated at max_tokens"
                        reasoning_tokens = _reasoning_tokens(raw)
                        if reasoning_tokens is not None:
                            message += (
                                f" (finish_reason={finish_reason}, "
                                f"reasoning_tokens={reasoning_tokens})"
                            )
                        else:
                            message += f" (finish_reason={finish_reason})"
                        raise ResponseLimitError(message)
                    try:
                        parsed_output = self._validate_output(content, schema)
                    except StructuredOutputError as error:
                        if (
                            not error.retryable
                            or profile.provider_id not in JSON_OBJECT_PROVIDERS
                            or attempt == MAX_STRUCTURED_ATTEMPTS - 1
                        ):
                            raise
                        await asyncio.sleep(
                            STRUCTURED_RETRY_BACKOFF_SECONDS * (2**attempt)
                        )
                        continue
                    self.usage.record(usage)
                    return ModelResponse(
                        profile_id=profile.id,
                        provider_id=profile.provider_id,
                        model=profile.model,
                        content=content,
                        parsed_output=parsed_output,
                        finish_reason=finish_reason,
                        usage=usage,
                    )
        except TimeoutError as error:
            raise ModelTimeoutError(
                "model request exceeded its total deadline"
            ) from error
        raise ProviderResponseError("model request failed")

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

    def embed(self, text: str, *, profile_id: str = "embedding") -> tuple[float, ...]:
        """Create one production embedding through the configured task profile."""
        if not text.strip():
            raise ValueError("embedding text must not be empty")
        profile = self.registry.get_profile(profile_id)
        if not profile.enabled:
            raise ModelConfigurationError(f"profile {profile.id!r} is disabled")
        if profile.task_type != "embedding":
            raise ProfileMismatchError(
                f"profile {profile.id!r} is for {profile.task_type}, not embedding"
            )
        payload = {"model": profile.model, "input": text, "encoding_format": "float"}
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise ResponseLimitError(
                f"embedding request exceeded {MAX_REQUEST_BYTES} bytes"
            )
        raw = self.transport.embed(payload, timeout_seconds=profile.timeout_seconds)
        data = raw.get("data")
        if (
            not isinstance(data, Sequence)
            or isinstance(data, (str, bytes))
            or not data
            or not isinstance(data[0], Mapping)
        ):
            raise ProviderResponseError("provider response has no embedding")
        vector = data[0].get("embedding")
        if (
            not isinstance(vector, Sequence)
            or isinstance(vector, (str, bytes))
            or not vector
            or any(not isinstance(item, (int, float)) for item in vector)
        ):
            raise ProviderResponseError("provider returned an invalid embedding")
        usage_value = raw.get("usage")
        if isinstance(usage_value, Mapping):
            prompt_tokens = usage_value.get("prompt_tokens", 0)
            total_tokens = usage_value.get("total_tokens", prompt_tokens)
            self.usage.record(
                ModelUsage(
                    prompt_tokens=(
                        prompt_tokens if isinstance(prompt_tokens, int) else 0
                    ),
                    total_tokens=total_tokens if isinstance(total_tokens, int) else 0,
                )
            )
        return tuple(float(item) for item in vector)
