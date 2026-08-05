import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from profile_factory import agent_profile as _profile

from story_engine.models.contracts import (
    Message,
    ModelRequest,
    ModelStreamChunk,
    ModelUsage,
)
from story_engine.models.errors import (
    ModelConfigurationError,
    ModelTimeoutError,
    ProfileMismatchError,
    ProviderResponseError,
    ResponseLimitError,
    StructuredOutputError,
)
from story_engine.models.gateway import (
    ModelGateway,
    OpenAICompatibleTransport,
    initial_budget,
)
from story_engine.models.registry import ProfileRegistry


class FakeTransport:
    def __init__(
        self,
        *contents: str,
        finish_reason: str = "stop",
        reasoning_tokens: int | None = None,
    ) -> None:
        self.contents = list(contents or ("confirmed prose",))
        self.finish_reason = finish_reason
        self.reasoning_tokens = reasoning_tokens
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(payload)
        content = self.contents.pop(0) if len(self.contents) > 1 else self.contents[0]
        usage: dict[str, Any] = {
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "total_tokens": 8,
        }
        if self.reasoning_tokens is not None:
            usage["completion_tokens_details"] = {
                "reasoning_tokens": self.reasoning_tokens
            }
        return {
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": self.finish_reason,
                }
            ],
            "usage": usage,
        }

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[ModelStreamChunk]:
        del timeout_seconds
        self.calls.append(payload)
        content = self.contents[0]
        midpoint = len(content) // 2
        yield ModelStreamChunk(delta=content[:midpoint])
        yield ModelStreamChunk(delta=content[midpoint:])
        yield ModelStreamChunk(
            done=True,
            usage=ModelUsage(
                prompt_tokens=5,
                completion_tokens=3,
                total_tokens=8,
            ),
        )


def _request(
    *,
    profile_id: str = "writer",
    task_type: str = "writer",
    output_schema: str | None = None,
) -> ModelRequest:
    return ModelRequest(
        profile_id=profile_id,
        task_type=task_type,  # type: ignore[arg-type]
        messages=(Message(role="user", content="Write the confirmed event."),),
        output_schema=output_schema,
        max_output_tokens=512,
        timeout_seconds=10,
        temperature=0.4,
    )


def _gateway(
    tmp_path: Path,
    transport: FakeTransport,
) -> tuple[ModelGateway, ProfileRegistry]:
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
        )
    )
    return ModelGateway(registry, transport), registry


def test_initial_budget_separates_choice_short_json_and_full_calls() -> None:
    assert initial_budget("choice", 4096) == 1024
    assert initial_budget("choice", 512) == 512
    assert initial_budget("short_json", 4096) == 2048
    assert initial_budget("short_json", 1024) == 1024
    assert initial_budget("full", 4096) == 4096


def test_agent_profile_model_change_is_used_by_next_request(
    tmp_path: Path,
) -> None:
    transport = FakeTransport("confirmed prose")
    gateway, registry = _gateway(tmp_path, transport)
    first = asyncio.run(gateway.complete(_request()))
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="cloud/cloud-model-id",
        )
    )
    second = asyncio.run(gateway.complete(_request()))

    assert first.content == second.content == "confirmed prose"
    assert transport.calls[0]["model"] == "test-provider/test-writer"
    assert transport.calls[1]["model"] == "cloud/cloud-model-id"
    assert first.model_ref == "test-provider/test-writer"
    assert second.model_ref == "cloud/cloud-model-id"


def test_unconfigured_profile_fails_before_the_jan_bridge(tmp_path: Path) -> None:
    transport = FakeTransport("unused")
    gateway = ModelGateway(ProfileRegistry(tmp_path / "models.json"), transport)

    with pytest.raises(ModelConfigurationError, match="has no model selected"):
        asyncio.run(gateway.complete(_request()))

    assert transport.calls == []


def test_structured_output_is_normalized_by_jan_bridge_contract(
    tmp_path: Path,
) -> None:
    schema = json.dumps(
        {
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"const": "accept"}},
            "additionalProperties": False,
        }
    )
    transport = FakeTransport('{"decision":"accept"}')
    gateway, _ = _gateway(tmp_path, transport)

    response = asyncio.run(gateway.complete(_request(output_schema=schema)))

    assert response.parsed_output == {"decision": "accept"}
    assert transport.calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "story_engine_output",
            "schema": json.loads(schema),
        },
    }
    assert "thinking" not in transport.calls[0]
    assert "reasoning_effort" not in transport.calls[0]


def test_invalid_json_retries_without_provider_specific_branching(
    tmp_path: Path,
) -> None:
    schema = json.dumps({"type": "object", "required": ["ok"]})
    transport = FakeTransport("not-json", '{"ok":true}')
    gateway, _ = _gateway(tmp_path, transport)

    response = asyncio.run(gateway.complete(_request(output_schema=schema)))

    assert response.parsed_output == {"ok": True}
    assert len(transport.calls) == 2


def test_unsupported_response_format_falls_back_to_prompt_only_json(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        if "response_format" in payload:
            return httpx.Response(
                400,
                json={
                    "error": {
                        "message": "This response_format type is unavailable now"
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": '{"decision":"accept"}'},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
        )
    )
    gateway = ModelGateway(
        registry,
        OpenAICompatibleTransport(
            "http://127.0.0.1:49152/v1",
            "",
            httpx.MockTransport(handler),
        ),
    )
    schema = json.dumps(
        {
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"const": "accept"}},
        }
    )

    response = asyncio.run(gateway.complete(_request(output_schema=schema)))

    assert response.parsed_output == {"decision": "accept"}
    assert len(calls) == 2
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert "response_format" not in calls[1]
    fallback_messages = calls[1]["messages"]
    assert fallback_messages[0]["role"] == "system"
    assert fallback_messages[1]["role"] == "user"
    assert "JSON SCHEMA" in fallback_messages[0]["content"]
    assert '"decision"' in fallback_messages[0]["content"]


def test_unrelated_provider_400_does_not_trigger_structured_fallback(
    tmp_path: Path,
) -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            400,
            json={"error": {"message": "model is unavailable"}},
        )

    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
        )
    )
    gateway = ModelGateway(
        registry,
        OpenAICompatibleTransport(
            "http://127.0.0.1:49152/v1",
            "",
            httpx.MockTransport(handler),
        ),
    )

    with pytest.raises(ProviderResponseError, match="model is unavailable"):
        asyncio.run(
            gateway.complete(
                _request(output_schema=json.dumps({"type": "object"}))
            )
        )
    assert attempts == 1


@pytest.mark.parametrize("content", ['{"wrong":true}', '{"ok":"bad"}'])
def test_schema_validation_failure_is_not_retried(
    tmp_path: Path,
    content: str,
) -> None:
    schema = json.dumps(
        {
            "type": "object",
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        }
    )
    transport = FakeTransport(content)
    gateway, _ = _gateway(tmp_path, transport)

    with pytest.raises(StructuredOutputError):
        asyncio.run(gateway.complete(_request(output_schema=schema)))
    assert len(transport.calls) == 1


def test_empty_structured_output_reports_error_after_bounded_retries(
    tmp_path: Path,
) -> None:
    gateway, _ = _gateway(tmp_path, FakeTransport("  \n"))
    with pytest.raises(StructuredOutputError, match="empty JSON content"):
        asyncio.run(
            gateway.complete(
                _request(output_schema=json.dumps({"type": "object"}))
            )
        )


def test_token_truncation_reports_bridge_reasoning_usage(tmp_path: Path) -> None:
    gateway, _ = _gateway(
        tmp_path,
        FakeTransport(
            '{"decision":',
            finish_reason="length",
            reasoning_tokens=4096,
        ),
    )
    with pytest.raises(ResponseLimitError, match="reasoning_tokens=4096"):
        asyncio.run(
            gateway.complete(
                _request(output_schema=json.dumps({"type": "object"}))
            )
        )


def test_profile_budget_is_authoritative_when_request_omits_tokens(
    tmp_path: Path,
) -> None:
    transport = FakeTransport("confirmed prose")
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=3072,
        )
    )
    gateway = ModelGateway(registry, transport)

    request = _request().model_copy(update={"max_output_tokens": None})
    asyncio.run(gateway.complete(request))

    assert transport.calls[0]["max_tokens"] == 3072


def test_provider_managed_output_omits_max_tokens(tmp_path: Path) -> None:
    transport = FakeTransport("confirmed prose")
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=4096,
        )
    )
    gateway = ModelGateway(registry, transport)

    request = _request().model_copy(
        update={
            "max_output_tokens": None,
            "output_token_limit": "provider",
        }
    )
    asyncio.run(gateway.complete(request))

    assert "max_tokens" not in transport.calls[0]


def test_reasoning_effort_comes_from_profile_and_request_override(
    tmp_path: Path,
) -> None:
    transport = FakeTransport("confirmed prose")
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            reasoning_effort="high",
        )
    )
    gateway = ModelGateway(registry, transport)

    asyncio.run(gateway.complete(_request()))
    asyncio.run(
        gateway.complete(_request().model_copy(update={"reasoning_effort": "low"}))
    )
    asyncio.run(
        gateway.complete(_request().model_copy(update={"reasoning_effort": None}))
    )

    assert transport.calls[0]["reasoning_effort"] == "high"
    assert transport.calls[1]["reasoning_effort"] == "low"
    assert transport.calls[2]["reasoning_effort"] == "high"


@pytest.mark.parametrize(
    "effort",
    ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
)
def test_reasoning_effort_matches_bridge_contract(
    tmp_path: Path,
    effort: str,
) -> None:
    transport = FakeTransport("confirmed prose")
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            reasoning_effort=effort,  # type: ignore[arg-type]
        )
    )
    gateway = ModelGateway(registry, transport)

    asyncio.run(gateway.complete(_request()))

    assert transport.calls[0]["reasoning_effort"] == effort


def test_null_reasoning_effort_is_omitted(tmp_path: Path) -> None:
    transport = FakeTransport("confirmed prose")
    gateway, _ = _gateway(tmp_path, transport)

    asyncio.run(
        gateway.complete(_request().model_copy(update={"reasoning_effort": None}))
    )

    assert "reasoning_effort" not in transport.calls[0]


class SequenceTransport:
    def __init__(
        self,
        calls: list[tuple[str, str, int | None]],
    ) -> None:
        self._calls = calls
        self.observed: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.observed.append(dict(payload))
        content, finish_reason, reasoning_tokens = self._calls.pop(0)
        usage: dict[str, Any] = {
            "prompt_tokens": 5,
            "completion_tokens": 3,
            "total_tokens": 8,
        }
        if reasoning_tokens is not None:
            usage["completion_tokens_details"] = {
                "reasoning_tokens": reasoning_tokens
            }
        return {
            "choices": [
                {
                    "message": {"content": content},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": usage,
        }


def test_first_content_deadline_uses_streaming_and_ignores_reasoning(
    tmp_path: Path,
) -> None:
    observed: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(json.loads(request.content))
        stream = "\n".join(
            (
                'data: {"model":"test-writer","choices":[{"delta":'
                '{"reasoning_content":"thinking"},"finish_reason":null}]}',
                "",
                'data: {"model":"test-writer","choices":[{"delta":'
                '{"content":"{\\"decision\\":\\"accept\\"}"},'
                '"finish_reason":null}]}',
                "",
                'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
                '"usage":{"prompt_tokens":5,"completion_tokens":9,'
                '"total_tokens":14,"completion_tokens_details":'
                '{"reasoning_tokens":4}}}',
                "",
                "data: [DONE]",
                "",
            )
        )
        return httpx.Response(
            200,
            text=stream,
            headers={"content-type": "text/event-stream"},
        )

    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
        )
    )
    gateway = ModelGateway(
        registry,
        OpenAICompatibleTransport(
            "http://127.0.0.1:49152/v1",
            "",
            httpx.MockTransport(handler),
        ),
    )
    schema = json.dumps(
        {
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"const": "accept"}},
        }
    )
    request = _request(output_schema=schema).model_copy(
        update={
            "max_output_tokens": None,
            "output_token_limit": "provider",
            "first_content_timeout_seconds": 300,
        }
    )

    response = asyncio.run(gateway.complete(request))

    assert response.parsed_output == {"decision": "accept"}
    assert response.usage.reasoning_tokens == 4
    assert observed[0]["stream"] is True
    assert observed[0]["stream_options"] == {"include_usage": True}
    assert "max_tokens" not in observed[0]


class DelayedContentStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield (
            b'data: {"choices":[{"delta":{"reasoning_content":"thinking"},'
            b'"finish_reason":null}]}\n\n'
        )
        await asyncio.sleep(1)
        yield b'data: {"choices":[{"delta":{"content":"late"}}]}\n\n'


def test_first_content_deadline_cancels_reasoning_only_stream() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=DelayedContentStream(),
            headers={"content-type": "text/event-stream"},
        )

    transport = OpenAICompatibleTransport(
        "http://127.0.0.1:49152/v1",
        "",
        httpx.MockTransport(handler),
    )

    with pytest.raises(
        ModelTimeoutError,
        match=r"no content token within 0\.01s",
    ):
        asyncio.run(
            transport.complete(
                {"model": "test-provider/test-writer", "messages": []},
                timeout_seconds=1,
                first_content_timeout_seconds=0.01,
            )
        )


def test_truncated_structured_output_retries_with_expanded_budget(
    tmp_path: Path,
) -> None:
    schema = json.dumps(
        {
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"const": "accept"}},
        }
    )
    transport = SequenceTransport(
        [
            ('{"decision":', "length", 256),
            ('{"decision":"accept"}', "stop", None),
        ]
    )
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=4096,
        )
    )
    gateway = ModelGateway(registry, transport)

    response = asyncio.run(
        gateway.complete(_request(output_schema=schema))
    )

    assert response.parsed_output == {"decision": "accept"}
    assert response.retry_count == 1
    assert response.finish_reason == "stop"
    assert transport.observed[0]["max_tokens"] == 512
    assert transport.observed[1]["max_tokens"] == 1024
    assert gateway.usage.totals().requests == 2
    assert gateway.usage.totals().total_tokens == 16


def test_free_text_truncation_retries_then_fails_loudly(tmp_path: Path) -> None:
    transport = SequenceTransport(
        [
            ("half a sentence", "length", None),
            ("still truncated", "length", None),
            ("truncated again", "length", None),
        ]
    )
    gateway, _ = _gateway(tmp_path, transport)

    with pytest.raises(ResponseLimitError, match="truncated at max_tokens") as caught:
        asyncio.run(gateway.complete(_request()))

    assert len(transport.observed) == 3
    assert gateway.usage.totals().requests == 3
    assert caught.value.retry_count == 2
    assert caught.value.finish_reason == "length"


def test_empty_free_text_is_retried_then_fails(tmp_path: Path) -> None:
    transport = FakeTransport("   ")
    gateway, _ = _gateway(tmp_path, transport)

    with pytest.raises(StructuredOutputError, match="empty text content") as caught:
        asyncio.run(gateway.complete(_request()))

    assert len(transport.calls) == 3
    assert caught.value.retry_count == 2
    assert gateway.usage.totals().requests == 3


def test_empty_free_text_recovers_on_retry(tmp_path: Path) -> None:
    transport = SequenceTransport(
        [
            ("", "stop", None),
            ("The keeper lights the lamp.", "stop", None),
        ]
    )
    gateway, _ = _gateway(tmp_path, transport)  # type: ignore[arg-type]

    response = asyncio.run(gateway.complete(_request()))

    assert response.content == "The keeper lights the lamp."
    assert response.retry_count == 1
    assert len(transport.observed) == 2


def test_usage_parses_reasoning_tokens_from_provider_details(
    tmp_path: Path,
) -> None:
    transport = FakeTransport("confirmed prose", reasoning_tokens=7)
    gateway, _ = _gateway(tmp_path, transport)

    response = asyncio.run(gateway.complete(_request()))

    assert response.usage.reasoning_tokens == 7


def test_task_mismatch_fails_before_the_jan_bridge(tmp_path: Path) -> None:
    transport = FakeTransport("unused")
    gateway, _ = _gateway(tmp_path, transport)
    with pytest.raises(ProfileMismatchError):
        asyncio.run(gateway.complete(_request(task_type="editor")))
    assert transport.calls == []


def test_streaming_records_usage(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path, FakeTransport("streamed prose"))

    async def collect() -> list[ModelStreamChunk]:
        return [chunk async for chunk in gateway.stream(_request())]

    chunks = asyncio.run(collect())
    assert "".join(chunk.delta for chunk in chunks) == "streamed prose"
    assert chunks[-1].done
    assert gateway.usage.totals().total_tokens == 8


def test_transport_retries_loopback_failures_and_sends_bridge_token() -> None:
    attempts = 0
    observed: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        observed.append((str(request.url), request.headers.get("Authorization")))
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    token = "b" * 64
    transport = OpenAICompatibleTransport(
        "http://127.0.0.1:49152/v1",
        token,
        httpx.MockTransport(handler),
    )
    result = asyncio.run(
        transport.complete({"messages": []}, timeout_seconds=2)
    )

    assert attempts == 3
    assert result["choices"]
    assert observed == [
        ("http://127.0.0.1:49152/v1/chat/completions", f"Bearer {token}")
    ] * 3


def test_transport_preserves_bridge_error_detail() -> None:
    transport = OpenAICompatibleTransport(
        "http://127.0.0.1:49152/v1",
        "",
        httpx.MockTransport(
            lambda _request: httpx.Response(
                400,
                json={"error": {"message": "normalized request was rejected"}},
            )
        ),
    )
    with pytest.raises(ProviderResponseError, match="normalized request was rejected"):
        asyncio.run(transport.complete({"messages": []}, timeout_seconds=2))


def test_bridge_timeout_is_not_retried() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("bridge stalled", request=request)

    transport = OpenAICompatibleTransport(
        "http://127.0.0.1:49152/v1",
        "",
        httpx.MockTransport(handler),
    )
    with pytest.raises(ModelTimeoutError, match="provider request timed out"):
        asyncio.run(transport.complete({"messages": []}, timeout_seconds=2))
    assert attempts == 1


def test_gateway_enforces_total_deadline(tmp_path: Path) -> None:
    class SlowTransport(FakeTransport):
        async def complete(
            self,
            payload: Mapping[str, Any],
            *,
            timeout_seconds: float,
        ) -> Mapping[str, Any]:
            del payload, timeout_seconds
            await asyncio.sleep(2)
            raise AssertionError("deadline did not cancel transport")

    async def exercise() -> None:
        gateway, _ = _gateway(tmp_path, SlowTransport("unused"))
        request = _request().model_copy(update={"timeout_seconds": 1})
        with pytest.raises(
            ModelTimeoutError,
            match="profile 'writer' exceeded its 1s total deadline",
        ):
            await gateway.complete(request)

    asyncio.run(exercise())
