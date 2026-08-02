import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from story_engine.concordia_adapter.language_model import JanGatewayLanguageModel
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.gateway import ModelGateway
from story_engine.models.registry import ProfileRegistry


class TextTransport:
    def __init__(self, content: str) -> None:
        self.content = content
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
                    "message": {"content": self.content},
                    "finish_reason": "stop",
                }
            ]
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
        raise AssertionError("language model tests do not stream")


def _schema() -> str:
    return json.dumps(
        {
            "type": "object",
            "required": ["summary"],
            "properties": {"summary": {"type": "string"}},
            "additionalProperties": False,
        },
        ensure_ascii=False,
    )


def _model(
    tmp_path: Path,
    content: str,
    output_schema: str | None,
) -> tuple[JanGatewayLanguageModel, TextTransport]:
    transport = TextTransport(content)
    gateway = ModelGateway(ProfileRegistry(tmp_path / "models.json"), transport)
    model = JanGatewayLanguageModel(
        gateway,
        profile_id="writer",
        task_type="writer",
        output_schema=output_schema,
    )
    return model, transport


def test_structured_sample_text_keeps_multiline_json(tmp_path: Path) -> None:
    content = '{\n  "summary": "a resolved outcome"\n}'
    model, _ = _model(tmp_path, content, _schema())

    result = model.sample_text(
        "Resolve the turn.",
        max_tokens=8192,
        timeout=120,
        terminators=("\n",),
    )

    assert result == content


def test_unstructured_sample_text_still_truncates_at_terminators(
    tmp_path: Path,
) -> None:
    model, _ = _model(tmp_path, "line one\nline two", None)

    result = model.sample_text("Write prose.", terminators=("\n",))

    assert result == "line one"
