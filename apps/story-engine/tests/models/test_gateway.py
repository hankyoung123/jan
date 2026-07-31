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
from story_engine.models.errors import ProfileMismatchError, StructuredOutputError
from story_engine.models.gateway import ModelGateway, OpenAICompatibleTransport
from story_engine.models.registry import ProfileRegistry


class FakeTransport:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        self.calls.append(payload)
        return {
            "choices": [
                {
                    "message": {"content": self.content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 3,
                "total_tokens": 8,
            },
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


def test_structured_output_is_parsed_and_validated(tmp_path: Path) -> None:
    schema = json.dumps(
        {
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"const": "accept"}},
            "additionalProperties": False,
        }
    )
    gateway, _ = _gateway(tmp_path, FakeTransport('{"decision":"accept"}'))

    response = asyncio.run(gateway.complete(_request(output_schema=schema)))

    assert response.parsed_output == {"decision": "accept"}
    assert response.usage.total_tokens == 8
    assert gateway.usage.totals().requests == 1


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
