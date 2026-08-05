import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from profile_factory import agent_profile as _profile

from story_engine.concordia_runtime.language_model import (
    JanConcordiaLanguageModel,
    ModelCallCancelledError,
)
from story_engine.domain.trace import ModelCallStatus
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.errors import ResponseLimitError
from story_engine.models.gateway import ModelGateway
from story_engine.models.registry import ProfileRegistry


class QueueTransport:
    def __init__(self, responses: tuple[str, ...]) -> None:
        self._responses = list(responses)
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(payload)
        return {
            "choices": [
                {
                    "message": {"content": self._responses.pop(0)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
            },
        }

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[ModelStreamChunk]:
        del payload, timeout_seconds
        if False:
            yield ModelStreamChunk()
        raise AssertionError("runtime language model tests do not stream")


def _gateway(tmp_path: Path, *responses: str) -> tuple[ModelGateway, QueueTransport]:
    transport = QueueTransport(tuple(responses))
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
        )
    )
    return (
        ModelGateway(registry, transport),
        transport,
    )


def test_runtime_language_model_emits_source_trace(tmp_path: Path) -> None:
    gateway, _ = _gateway(tmp_path, "A concise answer.")
    traces = []
    model = JanConcordiaLanguageModel(
        gateway,
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
        session_id="session:1",
        branch_id="main",
        step=2,
        actor_id="actor-a",
        component_ids=("knowledge", "identity"),
        source_record_ids=("memory:1",),
        trace_sink=traces.append,
    )

    assert model.sample_text("Answer the witness.", terminators=()) == (
        "A concise answer."
    )

    assert len(traces) == 1
    trace = traces[0]
    assert trace.status == ModelCallStatus.SUCCEEDED
    assert trace.profile_id == "writer"
    assert trace.session_id == "session:1"
    assert trace.component_ids == ("knowledge", "identity")
    assert trace.prompt_tokens == 10
    assert len(trace.prompt_sha256) == 64
    assert trace.finish_reason == "stop"
    assert trace.retry_count == 0


def test_runtime_language_model_choice_and_precancel(tmp_path: Path) -> None:
    gateway, transport = _gateway(tmp_path, json.dumps({"choice": "b"}))
    model = JanConcordiaLanguageModel(
        gateway,
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
    )

    assert model.sample_choice("Choose.", ("a", "b"))[:2] == (1, "b")

    cancellation = Event()
    cancellation.set()
    cancelled_traces = []
    cancelled = JanConcordiaLanguageModel(
        gateway,
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
        cancellation=cancellation,
        trace_sink=cancelled_traces.append,
    )
    with pytest.raises(ModelCallCancelledError):
        cancelled.sample_text("This must not reach the transport.")

    assert len(transport.calls) == 1
    assert cancelled_traces[0].status == ModelCallStatus.CANCELLED


def test_runtime_language_model_uses_profile_budget_and_reasoning_effort(
    tmp_path: Path,
) -> None:
    gateway, transport = _gateway(tmp_path, "A concise answer.")
    gateway.registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=3072,
            reasoning_effort="medium",
        )
    )
    model = JanConcordiaLanguageModel(
        gateway,
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
    )

    model.sample_text("Answer the witness.", terminators=())

    assert transport.calls[0]["max_tokens"] == 3072
    assert transport.calls[0]["reasoning_effort"] == "medium"


def test_choice_budget_comes_from_profile_instead_of_hardcoded_256(
    tmp_path: Path,
) -> None:
    gateway, transport = _gateway(tmp_path, json.dumps({"choice": "b"}))
    gateway.registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=2048,
        )
    )
    model = JanConcordiaLanguageModel(
        gateway,
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
    )

    model.sample_choice("Choose.", ("a", "b"))

    assert transport.calls[0]["max_tokens"] == 2048


def test_schema_model_starts_with_short_json_budget_and_response_format(
    tmp_path: Path,
) -> None:
    gateway, transport = _gateway(tmp_path, '{"decision":"accept"}')
    gateway.registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=4096,
        )
    )
    model = JanConcordiaLanguageModel(
        gateway,
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
        output_schema=json.dumps(
            {
                "type": "object",
                "required": ["decision"],
                "properties": {"decision": {"const": "accept"}},
            }
        ),
    )

    model.sample_text("Decide.", terminators=())

    assert transport.calls[0]["max_tokens"] == 4096
    assert transport.calls[0]["response_format"] == {
        "type": "json_schema",
        "json_schema": {
            "name": "story_engine_output",
            "schema": {
                "type": "object",
                "required": ["decision"],
                "properties": {"decision": {"const": "accept"}},
            },
        },
    }


def test_runtime_language_model_accepts_a_call_specific_json_schema(
    tmp_path: Path,
) -> None:
    gateway, transport = _gateway(tmp_path, '{"actor_ids":["agent-a"]}')
    model = JanConcordiaLanguageModel(
        gateway,
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
    )
    schema = {
        "type": "object",
        "required": ["actor_ids"],
        "properties": {
            "actor_ids": {
                "type": "array",
                "maxItems": 4,
                "items": {"enum": ["agent-a", "agent-b"]},
            }
        },
    }

    result = model.sample_json("Select the roster.", schema)

    assert result == {"actor_ids": ["agent-a"]}
    assert transport.calls[0]["response_format"]["json_schema"]["schema"] == schema


def test_free_text_starts_at_profile_ceiling(tmp_path: Path) -> None:
    gateway, transport = _gateway(tmp_path, "A concise answer.")
    gateway.registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=4096,
        )
    )
    model = JanConcordiaLanguageModel(
        gateway,
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
    )

    model.sample_text("Answer the witness.", terminators=())

    assert transport.calls[0]["max_tokens"] == 4096


def test_profile_resolver_and_effective_settings_are_read_per_call(
    tmp_path: Path,
) -> None:
    gateway, transport = _gateway(
        tmp_path,
        "first answer",
        "second answer",
    )
    gateway.registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/writer",
            max_output_tokens=4096,
            reasoning_effort="high",
        )
    )
    traces = []
    model = JanConcordiaLanguageModel(
        gateway,
        task_type="writer",
        content_locale="en-US",
        profile_resolver=lambda: "writer",
        trace_sink=traces.append,
    )

    model.sample_text("First.", terminators=())
    gateway.registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/writer-alt",
            max_output_tokens=2048,
            reasoning_effort="none",
            default_system_prompt="Use the newly saved Writer behavior.",
        )
    )
    model.sample_text("Second.", terminators=())

    assert transport.calls[0]["max_tokens"] == 4096
    assert transport.calls[1]["max_tokens"] == 2048
    assert transport.calls[1]["reasoning_effort"] == "none"
    assert transport.calls[1]["messages"][0]["content"] == (
        "Use the newly saved Writer behavior."
    )
    assert traces[0].profile_id == "writer"
    assert traces[0].model_ref == "test-provider/writer"
    assert traces[0].max_tokens == 4096
    assert traces[0].reasoning_effort == "high"
    assert traces[0].timeout_seconds == 60
    assert traces[1].profile_id == "writer"
    assert traces[1].max_tokens == 2048
    assert traces[1].reasoning_effort == "none"


class TruncatingTransport(QueueTransport):
    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        self.calls.append(payload)
        return {
            "choices": [
                {
                    "message": {"content": '{"choice":'},
                    "finish_reason": "length",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
            },
        }


class ReasoningTransport(QueueTransport):
    def __init__(self) -> None:
        super().__init__(("ignored",))

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        self.calls.append(payload)
        return {
            "choices": [
                {
                    "message": {"content": "A concise answer."},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 4,
                "total_tokens": 14,
                "completion_tokens_details": {"reasoning_tokens": 7},
            },
        }


class ExpandingTransport(QueueTransport):
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []
        self._attempt = 0

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(payload)
        self._attempt += 1
        if self._attempt == 1:
            content = '{"choice":'
            finish_reason = "length"
            reasoning_tokens = 256
        else:
            content = '{"choice":"b"}'
            finish_reason = "stop"
            reasoning_tokens = None
        usage: dict[str, Any] = {
            "prompt_tokens": 10,
            "completion_tokens": 4,
            "total_tokens": 14,
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


def test_failed_choice_records_usage_in_trace(tmp_path: Path) -> None:
    transport = TruncatingTransport(("ignored",))
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
        )
    )
    traces = []
    model = JanConcordiaLanguageModel(
        ModelGateway(registry, transport),
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
        trace_sink=traces.append,
    )

    with pytest.raises(ResponseLimitError):
        model.sample_choice("Choose.", ("a", "b"))

    assert len(traces) == 1
    assert traces[0].status == ModelCallStatus.FAILED
    assert traces[0].prompt_tokens == 10
    assert traces[0].completion_tokens == 4
    assert traces[0].finish_reason == "length"
    assert traces[0].retry_count == 0


def test_profile_ceiling_stops_truncation_without_hidden_budget_override(
    tmp_path: Path,
) -> None:
    transport = ExpandingTransport()
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
            max_output_tokens=4096,
        )
    )
    traces = []
    model = JanConcordiaLanguageModel(
        ModelGateway(registry, transport),
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
        trace_sink=traces.append,
    )

    with pytest.raises(ResponseLimitError):
        model.sample_choice("Choose.", ("a", "b"))

    assert len(transport.calls) == 1
    assert len(traces) == 1
    trace = traces[0]
    assert trace.status == ModelCallStatus.FAILED
    assert trace.finish_reason == "length"
    assert trace.retry_count == 0
    assert trace.max_tokens == 4096


def test_trace_records_provider_reasoning_tokens(tmp_path: Path) -> None:
    transport = ReasoningTransport()
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="writer",
            task_type="writer",
            model_ref="test-provider/test-writer",
        )
    )
    traces = []
    model = JanConcordiaLanguageModel(
        ModelGateway(registry, transport),
        profile_id="writer",
        task_type="writer",
        content_locale="en-US",
        trace_sink=traces.append,
    )

    model.sample_text("Answer.", terminators=())

    assert len(traces) == 1
    assert traces[0].finish_reason == "stop"
    assert traces[0].reasoning_tokens == 7
