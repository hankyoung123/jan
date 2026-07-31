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
    ProviderConfig,
)
from story_engine.models.errors import (
    MissingCredentialError,
    ProfileMismatchError,
    StructuredOutputError,
)
from story_engine.models.gateway import ModelGateway, OpenAICompatibleTransport
from story_engine.models.registry import ProfileRegistry
from story_engine.models.secrets import MemorySecretStore


class FakeTransport:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[tuple[ProviderConfig, Mapping[str, Any], str | None]] = []

    async def complete(
        self,
        provider: ProviderConfig,
        payload: Mapping[str, Any],
        *,
        credential: str | None,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        self.calls.append((provider, payload, credential))
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
        provider: ProviderConfig,
        payload: Mapping[str, Any],
        *,
        credential: str | None,
        timeout_seconds: int,
    ) -> AsyncIterator[ModelStreamChunk]:
        self.calls.append((provider, payload, credential))
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
) -> tuple[ModelGateway, ProfileRegistry, MemorySecretStore]:
    registry = ProfileRegistry(tmp_path / "models.json")
    secrets = MemorySecretStore()
    secrets.set("remote-openai", "top-secret")
    return ModelGateway(registry, secrets, transport), registry, secrets


def test_remote_and_local_profiles_use_the_same_gateway_contract(
    tmp_path: Path,
) -> None:
    transport = FakeTransport("confirmed prose")
    gateway, registry, _ = _gateway(tmp_path, transport)

    remote = asyncio.run(gateway.complete(_request()))
    local_profile = ModelProfile(
        id="local-writer",
        name="Local Writer",
        task_type="writer",
        provider_id="local-jan",
        model="qwen3-8b",
    )
    registry.upsert_profile(local_profile)
    local = asyncio.run(gateway.complete(_request(profile_id="local-writer")))

    assert remote.content == local.content == "confirmed prose"
    assert [call[0].kind for call in transport.calls] == ["remote", "local"]
    assert transport.calls[0][2] == "top-secret"
    assert transport.calls[1][2] is None


def test_structured_output_is_parsed_and_validated(tmp_path: Path) -> None:
    schema = json.dumps(
        {
            "type": "object",
            "required": ["decision"],
            "properties": {"decision": {"const": "accept"}},
            "additionalProperties": False,
        }
    )
    gateway, _, _ = _gateway(tmp_path, FakeTransport('{"decision":"accept"}'))

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
    gateway, _, _ = _gateway(tmp_path, FakeTransport(content))

    with pytest.raises(StructuredOutputError):
        asyncio.run(gateway.complete(_request(output_schema=schema)))


def test_missing_key_and_task_mismatch_fail_before_transport(tmp_path: Path) -> None:
    transport = FakeTransport("unused")
    gateway, _, secrets = _gateway(tmp_path, transport)
    secrets.delete("remote-openai")

    with pytest.raises(MissingCredentialError):
        asyncio.run(gateway.complete(_request()))

    secrets.set("remote-openai", "key")
    with pytest.raises(ProfileMismatchError):
        asyncio.run(gateway.complete(_request(task_type="editor")))
    assert transport.calls == []


def test_streaming_validates_final_output_and_records_usage(tmp_path: Path) -> None:
    gateway, _, _ = _gateway(tmp_path, FakeTransport("streamed prose"))

    async def collect() -> list[ModelStreamChunk]:
        return [chunk async for chunk in gateway.stream(_request())]

    chunks = asyncio.run(collect())

    assert "".join(chunk.delta for chunk in chunks) == "streamed prose"
    assert chunks[-1].done
    assert gateway.usage.totals().total_tokens == 8


def test_openai_transport_retries_transient_failures_without_leaking_key() -> None:
    attempts = 0
    observed_authorization: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        observed_authorization.append(request.headers.get("Authorization"))
        if attempts < 3:
            return httpx.Response(503)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )

    provider = ProviderConfig(
        id="remote",
        name="Remote",
        kind="remote",
        base_url="https://models.example/v1",
        requires_api_key=True,
    )
    transport = OpenAICompatibleTransport(httpx.MockTransport(handler))

    result = asyncio.run(
        transport.complete(
            provider,
            {"model": "test", "messages": []},
            credential="secret-key",
            timeout_seconds=2,
        )
    )

    assert attempts == 3
    assert result["choices"]
    assert observed_authorization == ["Bearer secret-key"] * 3
