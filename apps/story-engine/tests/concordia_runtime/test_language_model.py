import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from threading import Event
from typing import Any

import pytest

from story_engine.concordia_runtime.language_model import (
    JanConcordiaLanguageModel,
    ModelCallCancelledError,
)
from story_engine.domain.trace import ModelCallStatus
from story_engine.models.contracts import ModelProfile, ModelStreamChunk
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
        ModelProfile(
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
