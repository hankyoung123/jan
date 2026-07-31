import asyncio
import json
from collections.abc import AsyncIterator, Mapping, Sequence
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
    ProviderConfig,
    UsageTotals,
)
from story_engine.models.errors import (
    MissingCredentialError,
    ModelConfigurationError,
    ModelTimeoutError,
    ProfileMismatchError,
    ProviderResponseError,
    ResponseLimitError,
    StructuredOutputError,
)
from story_engine.models.registry import ProfileRegistry
from story_engine.models.secrets import SecretStore

MAX_REQUEST_BYTES = 1_048_576
MAX_RESPONSE_BYTES = 1_048_576
MAX_ATTEMPTS = 3


class ModelTransport(Protocol):
    async def complete(
        self,
        provider: ProviderConfig,
        payload: Mapping[str, Any],
        *,
        credential: str | None,
        timeout_seconds: int,
    ) -> Mapping[str, Any]: ...

    def stream(
        self,
        provider: ProviderConfig,
        payload: Mapping[str, Any],
        *,
        credential: str | None,
        timeout_seconds: int,
    ) -> AsyncIterator[ModelStreamChunk]: ...


class _TransientProviderError(Exception):
    pass


def _headers(credential: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    return headers


def _endpoint(provider: ProviderConfig, resource: str) -> str:
    return f"{provider.base_url.rstrip('/')}/{resource.lstrip('/')}"


async def _read_limited(response: httpx.Response) -> bytes:
    content = bytearray()
    async for chunk in response.aiter_bytes():
        content.extend(chunk)
        if len(content) > MAX_RESPONSE_BYTES:
            raise ResponseLimitError(
                f"provider response exceeded {MAX_RESPONSE_BYTES} bytes"
            )
    return bytes(content)


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code == 429 or response.status_code >= 500:
        raise _TransientProviderError(f"provider returned HTTP {response.status_code}")
    if response.is_error:
        raise ProviderResponseError(f"provider returned HTTP {response.status_code}")


class OpenAICompatibleTransport:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _client(self, timeout_seconds: int) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            transport=self._transport,
        )

    async def complete(
        self,
        provider: ProviderConfig,
        payload: Mapping[str, Any],
        *,
        credential: str | None,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        for attempt in range(MAX_ATTEMPTS):
            try:
                async with (
                    self._client(timeout_seconds) as client,
                    client.stream(
                        "POST",
                        _endpoint(provider, "chat/completions"),
                        headers=_headers(credential),
                        json=payload,
                    ) as response,
                ):
                    _raise_for_status(response)
                    content = await _read_limited(response)
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ProviderResponseError("provider response must be an object")
                return cast(dict[str, Any], parsed)
            except (TimeoutError, httpx.TimeoutException) as error:
                if attempt == MAX_ATTEMPTS - 1:
                    raise ModelTimeoutError("model request timed out") from error
            except (httpx.NetworkError, _TransientProviderError) as error:
                if attempt == MAX_ATTEMPTS - 1:
                    raise ProviderResponseError(
                        "provider was unavailable after retries"
                    ) from error
            except json.JSONDecodeError as error:
                raise ProviderResponseError("provider returned invalid JSON") from error
            await asyncio.sleep(0.1 * (2**attempt))
        raise ProviderResponseError("provider request failed")

    async def stream(
        self,
        provider: ProviderConfig,
        payload: Mapping[str, Any],
        *,
        credential: str | None,
        timeout_seconds: int,
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
                    _endpoint(provider, "chat/completions"),
                    headers=_headers(credential),
                    json=streamed_payload,
                ) as response,
            ):
                _raise_for_status(response)
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
        secrets: SecretStore,
        transport: ModelTransport | None = None,
        usage: UsageTracker | None = None,
    ) -> None:
        self.registry = registry
        self.secrets = secrets
        self.transport = transport or OpenAICompatibleTransport()
        self.usage = usage or UsageTracker()

    def _resolve(
        self, request: ModelRequest
    ) -> tuple[ModelProfile, ProviderConfig, str | None]:
        profile = self.registry.get_profile(request.profile_id)
        if not profile.enabled:
            raise ModelConfigurationError(f"profile {profile.id!r} is disabled")
        if profile.task_type != request.task_type:
            raise ProfileMismatchError(
                f"profile {profile.id!r} is for {profile.task_type}, "
                f"not {request.task_type}"
            )
        provider = self.registry.get_provider(profile.provider_id)
        credential = self.secrets.get(provider.id)
        if provider.requires_api_key and not credential:
            raise MissingCredentialError(
                f"provider {provider.id!r} requires an API key"
            )
        return profile, provider, credential

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
        payload: dict[str, Any] = {
            "model": profile.model,
            "messages": [
                message.model_dump(mode="json") for message in request.messages
            ],
            "max_tokens": request.max_output_tokens,
        }
        temperature = (
            request.temperature
            if request.temperature is not None
            else profile.temperature
        )
        if temperature is not None:
            payload["temperature"] = temperature
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
        try:
            parsed: JsonValue = json.loads(content)
        except json.JSONDecodeError as error:
            raise StructuredOutputError("model output is not valid JSON") from error
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
        profile, provider, credential = self._resolve(request)
        schema = self._schema(request)
        payload = self._payload(request, profile, schema)
        raw = await self.transport.complete(
            provider,
            payload,
            credential=credential,
            timeout_seconds=request.timeout_seconds,
        )
        content, finish_reason, usage = self._parse_content(raw)
        parsed_output = self._validate_output(content, schema)
        self.usage.record(usage)
        return ModelResponse(
            profile_id=profile.id,
            provider_id=provider.id,
            model=profile.model,
            content=content,
            parsed_output=parsed_output,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamChunk]:
        profile, provider, credential = self._resolve(request)
        schema = self._schema(request)
        payload = self._payload(request, profile, schema)
        content: list[str] = []
        usage = ModelUsage()
        size = 0
        async for chunk in self.transport.stream(
            provider,
            payload,
            credential=credential,
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
