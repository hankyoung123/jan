import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from story_engine.models.contracts import (
    Message,
    ModelProfile,
    ModelRequest,
    ModelStreamChunk,
    ModelUsage,
)
from story_engine.models.errors import (
    ModelTimeoutError,
    ProfileMismatchError,
    ProviderResponseError,
    ResponseLimitError,
    StructuredOutputError,
)
from story_engine.models.gateway import ModelGateway, OpenAICompatibleTransport
from story_engine.models.registry import ProfileRegistry


class FakeTransport:
    def __init__(
        self,
        content: str,
        *,
        finish_reason: str = "stop",
        reasoning_tokens: int | None = None,
    ) -> None:
        self.content = content
        self.finish_reason = finish_reason
        self.reasoning_tokens = reasoning_tokens
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        self.calls.append(payload)
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
                    "message": {"content": self.content},
                    "finish_reason": self.finish_reason,
                }
            ],
            "usage": usage,
        }

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[ModelStreamChunk]:
        self.calls.append(payload)
        midpoint = len(self.content) // 2
        yield ModelStreamChunk(delta=self.content[:midpoint])
        yield ModelStreamChunk(delta=self.content[midpoint:])
        yield ModelStreamChunk(
            done=True,
            usage=ModelUsage(
                prompt_tokens=5,
                completion_tokens=3,
                total_tokens=8,
            ),
        )

    def embed(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(payload)
        return {
            "data": [{"embedding": [0.25, 0.5, 0.75]}],
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        }


class QueuedContentTransport(FakeTransport):
    def __init__(self, *contents: str) -> None:
        super().__init__(contents[0] if contents else "")
        self.contents = list(contents)

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        self.content = self.contents.pop(0)
        return await super().complete(payload, timeout_seconds=timeout_seconds)


def _request(
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
    return ModelGateway(registry, transport), registry


def _deepseek_gateway(
    tmp_path: Path,
    transport: FakeTransport,
) -> tuple[ModelGateway, ProfileRegistry]:
    gateway, registry = _gateway(tmp_path, transport)
    registry.upsert_profile(
        ModelProfile(
            id="deepseek-writer",
            name="DeepSeek Writer",
            task_type="writer",
            provider_id="deepseek",
            model="deepseek-v4-flash",
        )
    )
    return gateway, registry


def test_remote_and_local_profiles_use_the_same_jan_bridge_contract(
    tmp_path: Path,
) -> None:
    transport = FakeTransport("confirmed prose")
    gateway, registry = _gateway(tmp_path, transport)

    remote = asyncio.run(gateway.complete(_request()))
    local_profile = ModelProfile(
        id="local-writer",
        name="Local Writer",
        task_type="writer",
        provider_id="llamacpp",
        model="qwen3-8b",
    )
    registry.upsert_profile(local_profile)
    local = asyncio.run(gateway.complete(_request(profile_id="local-writer")))

    assert remote.content == local.content == "confirmed prose"
    assert [call["model"] for call in transport.calls] == [
        "gpt-5-mini",
        "qwen3-8b",
    ]
    assert remote.provider_id == "openai"
    assert local.provider_id == "llamacpp"


def test_embedding_uses_configured_embedding_profile(tmp_path: Path) -> None:
    transport = FakeTransport("unused")
    gateway, _ = _gateway(tmp_path, transport)

    vector = gateway.embed("the lighthouse lens")

    assert vector == (0.25, 0.5, 0.75)
    assert transport.calls[0] == {
        "model": "bge-m3",
        "input": "the lighthouse lens",
        "encoding_format": "float",
    }
    assert gateway.usage.totals().prompt_tokens == 2


def test_structured_output_is_parsed_and_validated(tmp_path: Path) -> None:
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
    assert response.usage.total_tokens == 8
    assert gateway.usage.totals().requests == 1
    assert transport.calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "story_engine_output",
            "schema": json.loads(schema),
        },
    }


def test_deepseek_uses_documented_json_object_contract(tmp_path: Path) -> None:
    schema = json.dumps(
        {
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"const": "accept"}},
            "additionalProperties": False,
        }
    )
    transport = FakeTransport('{"decision":"accept"}')
    gateway, registry = _gateway(tmp_path, transport)
    registry.upsert_profile(
        ModelProfile(
            id="deepseek-editor",
            name="DeepSeek Editor",
            task_type="editor",
            provider_id="deepseek",
            model="deepseek-v4-flash",
        )
    )

    response = asyncio.run(
        gateway.complete(
            _request(
                profile_id="deepseek-editor",
                task_type="editor",
                output_schema=schema,
            )
        )
    )

    assert response.parsed_output == {"decision": "accept"}
    assert transport.calls[0]["response_format"] == {"type": "json_object"}
    system_message = transport.calls[0]["messages"][0]
    assert system_message["role"] == "system"
    assert "valid JSON object" in system_message["content"]
    assert '"decision"' in system_message["content"]


def test_deepseek_json_object_prompt_requires_single_line_output(
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
    gateway, _ = _deepseek_gateway(tmp_path, transport)

    asyncio.run(
        gateway.complete(_request(profile_id="deepseek-writer", output_schema=schema))
    )

    system_message = transport.calls[0]["messages"][0]
    assert "single compact line" in system_message["content"]
    assert "no line breaks" in system_message["content"]


def test_deepseek_retries_empty_json_content_then_succeeds(
    tmp_path: Path,
) -> None:
    transport = QueuedContentTransport("  \n", '{"ok": true}')
    gateway, _ = _deepseek_gateway(tmp_path, transport)

    response = asyncio.run(
        gateway.complete(
            _request(
                profile_id="deepseek-writer",
                output_schema=json.dumps({"type": "object"}),
            )
        )
    )

    assert response.parsed_output == {"ok": True}
    assert len(transport.calls) == 2
    assert gateway.usage.totals().requests == 1


def test_deepseek_retries_invalid_json_then_succeeds(tmp_path: Path) -> None:
    transport = QueuedContentTransport("not-json", '{"ok": true}')
    gateway, _ = _deepseek_gateway(tmp_path, transport)

    response = asyncio.run(
        gateway.complete(
            _request(
                profile_id="deepseek-writer",
                output_schema=json.dumps({"type": "object"}),
            )
        )
    )

    assert response.parsed_output == {"ok": True}
    assert len(transport.calls) == 2


def test_deepseek_structured_retries_exhaust_after_three_attempts(
    tmp_path: Path,
) -> None:
    transport = QueuedContentTransport("  \n", "  \n", "  \n")
    gateway, _ = _deepseek_gateway(tmp_path, transport)

    with pytest.raises(StructuredOutputError, match="empty JSON content"):
        asyncio.run(
            gateway.complete(
                _request(
                    profile_id="deepseek-writer",
                    output_schema=json.dumps({"type": "object"}),
                )
            )
        )

    assert len(transport.calls) == 3


def test_deepseek_does_not_retry_schema_validation_failures(
    tmp_path: Path,
) -> None:
    schema = json.dumps(
        {
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"const": "accept"}},
        }
    )
    transport = QueuedContentTransport('{"decision":"reject"}', '{"decision":"accept"}')
    gateway, _ = _deepseek_gateway(tmp_path, transport)

    with pytest.raises(StructuredOutputError, match="schema validation"):
        asyncio.run(
            gateway.complete(
                _request(profile_id="deepseek-writer", output_schema=schema)
            )
        )

    assert len(transport.calls) == 1


def test_non_deepseek_profiles_do_not_retry_invalid_json(tmp_path: Path) -> None:
    transport = QueuedContentTransport("not-json", '{"ok": true}')
    gateway, _ = _gateway(tmp_path, transport)

    with pytest.raises(StructuredOutputError, match="not valid JSON"):
        asyncio.run(
            gateway.complete(_request(output_schema=json.dumps({"type": "object"})))
        )

    assert len(transport.calls) == 1


def test_deepseek_disabled_thinking_is_sent_without_reasoning_effort(
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
    gateway, registry = _gateway(tmp_path, transport)
    registry.upsert_profile(
        ModelProfile(
            id="deepseek-resolver",
            name="DeepSeek Resolver",
            task_type="game_master",
            provider_id="deepseek",
            model="deepseek-v4-flash",
            reasoning_effort="disabled",
        )
    )

    asyncio.run(
        gateway.complete(
            _request(
                profile_id="deepseek-resolver",
                task_type="game_master",
                output_schema=schema,
            )
        )
    )

    assert transport.calls[0]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in transport.calls[0]


@pytest.mark.parametrize("level", ["low", "high", "max"])
def test_deepseek_reasoning_effort_levels_are_sent(tmp_path: Path, level: str) -> None:
    schema = json.dumps(
        {
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"const": "accept"}},
            "additionalProperties": False,
        }
    )
    transport = FakeTransport('{"decision":"accept"}')
    gateway, registry = _gateway(tmp_path, transport)
    registry.upsert_profile(
        ModelProfile(
            id="deepseek-resolver",
            name="DeepSeek Resolver",
            task_type="game_master",
            provider_id="deepseek",
            model="deepseek-v4-flash",
            reasoning_effort=level,  # type: ignore[arg-type]
        )
    )

    asyncio.run(
        gateway.complete(
            _request(
                profile_id="deepseek-resolver",
                task_type="game_master",
                output_schema=schema,
            )
        )
    )

    assert transport.calls[0]["reasoning_effort"] == level
    assert transport.calls[0]["thinking"] == {"type": "enabled"}


def test_deepseek_thinking_expands_token_budget_and_omits_temperature(
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
    gateway, registry = _deepseek_gateway(tmp_path, transport)
    registry.upsert_profile(
        ModelProfile(
            id="deepseek-editor",
            name="DeepSeek Editor",
            task_type="editor",
            provider_id="deepseek",
            model="deepseek-v4-flash",
            reasoning_effort="high",
        )
    )

    asyncio.run(
        gateway.complete(
            _request(
                profile_id="deepseek-editor",
                task_type="editor",
                output_schema=schema,
            ).model_copy(update={"max_output_tokens": 2048, "temperature": 0.1})
        )
    )

    call = transport.calls[0]
    assert call["max_tokens"] == 16384
    assert "temperature" not in call
    assert call["reasoning_effort"] == "high"
    assert call["thinking"] == {"type": "enabled"}


def test_deepseek_disabled_thinking_keeps_budget_and_temperature(
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
    gateway, registry = _deepseek_gateway(tmp_path, transport)
    registry.upsert_profile(
        ModelProfile(
            id="deepseek-editor",
            name="DeepSeek Editor",
            task_type="editor",
            provider_id="deepseek",
            model="deepseek-v4-flash",
            reasoning_effort="disabled",
        )
    )

    asyncio.run(
        gateway.complete(
            _request(
                profile_id="deepseek-editor",
                task_type="editor",
                output_schema=schema,
            ).model_copy(update={"max_output_tokens": 2048, "temperature": 0.1})
        )
    )

    call = transport.calls[0]
    assert call["max_tokens"] == 2048
    assert call["temperature"] == 0.1
    assert call["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in call


def test_non_deepseek_profiles_never_send_thinking_params(tmp_path: Path) -> None:
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

    asyncio.run(gateway.complete(_request(output_schema=schema)))

    assert "thinking" not in transport.calls[0]
    assert "reasoning_effort" not in transport.calls[0]


@pytest.mark.parametrize("content", ["not-json", '{"decision":"reject"}'])
def test_structured_output_rejects_parse_and_schema_failures(
    tmp_path: Path,
    content: str,
) -> None:
    schema = json.dumps(
        {
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"const": "accept"}},
        }
    )
    gateway, _ = _gateway(tmp_path, FakeTransport(content))

    with pytest.raises(StructuredOutputError):
        asyncio.run(gateway.complete(_request(output_schema=schema)))


def test_structured_output_reports_empty_provider_content(tmp_path: Path) -> None:
    schema = json.dumps({"type": "object"})
    gateway, _ = _gateway(tmp_path, FakeTransport("  \n"))

    with pytest.raises(StructuredOutputError, match="empty JSON content"):
        asyncio.run(gateway.complete(_request(output_schema=schema)))


def test_structured_output_reports_token_truncation(tmp_path: Path) -> None:
    schema = json.dumps({"type": "object"})
    gateway, _ = _gateway(
        tmp_path,
        FakeTransport('{"decision":', finish_reason="length"),
    )

    with pytest.raises(ResponseLimitError, match="truncated at max_tokens"):
        asyncio.run(gateway.complete(_request(output_schema=schema)))


def test_truncation_error_reports_reasoning_tokens_when_available(
    tmp_path: Path,
) -> None:
    schema = json.dumps({"type": "object"})
    gateway, _ = _gateway(
        tmp_path,
        FakeTransport(
            '{"decision":',
            finish_reason="length",
            reasoning_tokens=4096,
        ),
    )

    with pytest.raises(ResponseLimitError, match="reasoning_tokens=4096"):
        asyncio.run(gateway.complete(_request(output_schema=schema)))


def test_task_mismatch_fails_before_the_jan_bridge(tmp_path: Path) -> None:
    transport = FakeTransport("unused")
    gateway, _ = _gateway(tmp_path, transport)

    with pytest.raises(ProfileMismatchError):
        asyncio.run(gateway.complete(_request(task_type="editor")))
    assert transport.calls == []


def test_streaming_validates_final_output_and_records_usage(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path, FakeTransport("streamed prose"))

    async def collect() -> list[ModelStreamChunk]:
        return [chunk async for chunk in gateway.stream(_request())]

    chunks = asyncio.run(collect())

    assert "".join(chunk.delta for chunk in chunks) == "streamed prose"
    assert chunks[-1].done
    assert gateway.usage.totals().total_tokens == 8


def test_transport_retries_bridge_failures_and_authenticates_only_to_loopback() -> None:
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
        transport.complete(
            {"model": "test", "messages": []},
            timeout_seconds=2,
        )
    )

    assert attempts == 3
    assert result["choices"]
    assert (
        observed
        == [("http://127.0.0.1:49152/v1/chat/completions", f"Bearer {token}")] * 3
    )


def test_transport_preserves_provider_error_message() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": {"message": "response_format.type must be text or json_object"}
            },
        )

    transport = OpenAICompatibleTransport(
        "http://127.0.0.1:49152/v1",
        "",
        httpx.MockTransport(handler),
    )

    with pytest.raises(
        ProviderResponseError,
        match=r"response_format\.type must be text or json_object",
    ):
        asyncio.run(
            transport.complete(
                {"model": "test", "messages": []},
                timeout_seconds=2,
            )
        )


def test_provider_timeout_is_not_retried_to_avoid_duplicate_billing() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("provider stalled", request=request)

    transport = OpenAICompatibleTransport(
        "http://127.0.0.1:49152/v1",
        "",
        httpx.MockTransport(handler),
    )

    with pytest.raises(ModelTimeoutError, match="provider request timed out"):
        asyncio.run(
            transport.complete(
                {"model": "test", "messages": []},
                timeout_seconds=2,
            )
        )

    assert attempts == 1


def test_gateway_enforces_one_total_deadline_across_transport_work(
    tmp_path: Path,
) -> None:
    class SlowTransport(FakeTransport):
        async def complete(
            self,
            payload: Mapping[str, Any],
            *,
            timeout_seconds: int,
        ) -> Mapping[str, Any]:
            del payload, timeout_seconds
            await asyncio.sleep(2)
            raise AssertionError("deadline did not cancel the transport")

    async def exercise() -> None:
        gateway, _registry = _gateway(tmp_path, SlowTransport("unused"))
        request = _request().model_copy(update={"timeout_seconds": 1})
        with pytest.raises(ModelTimeoutError, match="total deadline"):
            await gateway.complete(request)

    asyncio.run(exercise())
