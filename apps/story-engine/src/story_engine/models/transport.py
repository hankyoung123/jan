"""Provider transport implementations and their narrow Gateway boundary."""

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Protocol, cast

import httpx

from story_engine.models.contracts import ModelStreamChunk, ModelUsage
from story_engine.models.errors import (
    ModelConfigurationError,
    ModelTimeoutError,
    ProviderResponseError,
    ResponseLimitError,
    UnsupportedResponseFormatError,
)

ModelPartSink = Callable[[Literal["reasoning", "text"], str], None]

MAX_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 1_048_576
MAX_SSE_TRANSPORT_BYTES = 16 * MAX_RESPONSE_BYTES
MAX_PROVIDER_ERROR_DETAIL_CHARS = 1_000


class ModelTransport(Protocol):
    """Provider transport contract; policy and validation stay in ModelGateway."""

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
                            usage = usage_from_mapping(event_usage)
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


def usage_from_mapping(value: Mapping[str, Any]) -> ModelUsage:
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
