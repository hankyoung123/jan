import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from story_engine.domain.message import ModelMessageContext
from story_engine.domain.wiki import (
    WikiPage,
    WikiSource,
    WikiSourceKind,
    WikiUpdateProposal,
)
from story_engine.models.contracts import ModelRequest, ModelResponse
from story_engine.models.errors import StructuredOutputError
from story_engine.models.registry import default_registry
from story_engine.wiki.consolidator import (
    MAX_CONSOLIDATION_ATTEMPTS,
    GatewayWikiConsolidator,
    WikiProtocolError,
)


class FakeGateway:
    def __init__(self, *responses: ModelResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[ModelRequest] = []
        self.contexts: list[ModelMessageContext | None] = []
        profile = next(
            item
            for item in default_registry().profiles
            if item.agent_type == "wiki_maintainer"
        )
        self.registry = SimpleNamespace(get_profile=lambda _agent_type: profile)

    async def complete(
        self,
        request: ModelRequest,
        *,
        context: ModelMessageContext | None = None,
    ) -> ModelResponse:
        self.calls.append(request)
        self.contexts.append(context)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _response(output: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        profile_id="wiki_maintainer",
        model_ref="test-provider/test-wiki",
        content="",
        parsed_output=output,
        finish_reason="stop",
    )


def _valid_proposal() -> dict[str, Any]:
    return {
        "updates": [
            {
                "page_id": "state",
                "content": "The lighthouse is dark.",
                "source_refs": [0],
            }
        ]
    }


def _state_page() -> WikiPage:
    return WikiPage(
        path="world/state.md",
        branch_id="main",
        updated_at_step=0,
        content="# Current State\n\nThe harbor sleeps.",
    )


def _source() -> WikiSource:
    return WikiSource(
        source_id="event:session:1:1",
        kind=WikiSourceKind.EVENT,
        branch_id="main",
        step=1,
        content="The lighthouse is dark.",
    )


def _consolidate(gateway: FakeGateway):
    return asyncio.run(
        GatewayWikiConsolidator(gateway).consolidate(  # type: ignore[arg-type]
            project_id="north-star",
            session_id="session:1",
            step=1,
            branch_id="main",
            subject_id=None,
            pages=(_state_page(),),
            sources=(_source(),),
            content_locale="en-US",
        )
    )


def test_wiki_output_schema_contains_only_lightweight_proposal_fields() -> None:
    schema = WikiUpdateProposal.model_json_schema()
    update = schema["$defs"]["WikiUpdate"]

    assert set(schema["properties"]) == {"updates"}
    assert set(update["properties"]) == {"page_id", "content", "source_refs"}
    assert update["additionalProperties"] is False
    serialized = str(schema)
    for forbidden in (
        "path",
        "operation",
        "visibility",
        "expected_revision",
        "expected_content_hash",
        "source_ids",
        "section",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    "invalid,error_fragment",
    [
        (
            {
                "updates": [
                    {
                        "page_id": "index",
                        "content": "Do not edit the index.",
                        "source_refs": [0],
                    }
                ]
            },
            "unknown page_id 'index'",
        ),
        (
            {
                "updates": [
                    {
                        "page_id": "state",
                        "content": "Bad section field.",
                        "source_refs": [0],
                        "section": "Invented Heading",
                    }
                ]
            },
            "section",
        ),
        (
            {
                "updates": [
                    {
                        "page_id": "missing",
                        "content": "Missing page.",
                        "source_refs": [0],
                    }
                ]
            },
            "unknown page_id 'missing'",
        ),
        (
            {
                "updates": [
                    {
                        "page_id": "state",
                        "content": "Unknown source.",
                        "source_refs": [99],
                    }
                ]
            },
            "unknown source_refs [99]",
        ),
    ],
)
def test_consolidator_retries_invalid_proposals_with_exact_constraints(
    invalid: dict[str, Any],
    error_fragment: str,
) -> None:
    gateway = FakeGateway(_response(invalid), _response(_valid_proposal()))

    patches = _consolidate(gateway)

    assert len(patches) == 1
    assert patches[0].path == "world/state.md"
    assert patches[0].operation.value == "append_history"
    assert patches[0].source_ids == ("event:session:1:1",)
    assert patches[0].proposal_page_id == "state"
    assert patches[0].proposal_source_refs == (0,)
    assert len(gateway.calls) == 2
    correction = gateway.calls[1].messages[2].content
    assert error_fragment in correction
    assert "Allowed page_ids: ['state']" in correction
    assert "Allowed source_refs: [0]" in correction


def test_consolidator_fails_as_protocol_error_after_bounded_retries() -> None:
    gateway = FakeGateway(
        StructuredOutputError("model output failed schema validation"),
        StructuredOutputError("model output failed schema validation"),
    )

    with pytest.raises(WikiProtocolError, match="failed after 2 attempts"):
        _consolidate(gateway)

    assert len(gateway.calls) == MAX_CONSOLIDATION_ATTEMPTS


def test_consolidator_prompt_hides_paths_and_real_source_ids() -> None:
    gateway = FakeGateway(_response(_valid_proposal()))

    patches = _consolidate(gateway)

    prompt = gateway.calls[0].messages[2].content
    schema = gateway.calls[0].output_schema or ""
    assert "world/state.md" not in prompt
    assert "event:session:1:1" not in prompt
    assert "index.md" not in prompt
    assert "log.md" not in prompt
    assert "SCHEMA.md" not in prompt
    assert '"page_id": "state"' in prompt
    assert '"source_ref": 0' in prompt
    assert "WikiPatch" not in schema
    assert patches[0].content == "## Step 1\n\nThe lighthouse is dark."
    assert gateway.contexts[0] is not None
    assert gateway.contexts[0].stage == "wiki"
