import asyncio
from typing import Any

import pytest

from story_engine.domain.wiki import WikiPage, WikiSource, WikiSourceKind
from story_engine.models.contracts import ModelRequest, ModelResponse
from story_engine.models.errors import StructuredOutputError
from story_engine.wiki.consolidator import (
    MAX_CONSOLIDATION_ATTEMPTS,
    GatewayWikiConsolidator,
    _wiki_output_schema,
)


class FakeGateway:
    def __init__(self, *responses: ModelResponse | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _response(patches: dict[str, Any]) -> ModelResponse:
    return ModelResponse(
        profile_id="wiki-maintenance",
        model_ref="test-provider/test-wiki",
        content="",
        parsed_output=patches,
        finish_reason="stop",
    )


def _valid_patches() -> dict[str, Any]:
    return {
        "patches": [
            {
                "path": "world/state.md",
                "operation": "replace_section",
                "section": "Current State",
                "content": "The lighthouse is dark.",
                "source_ids": ["source:0"],
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


def test_wiki_output_schema_ties_section_to_operation() -> None:
    schema: dict[str, Any] = _wiki_output_schema()
    patch_schema = schema["properties"]["patches"]["items"]
    assert isinstance(patch_schema, dict)
    all_of = patch_schema["allOf"]
    assert isinstance(all_of, list)
    operations: set[str] = set()
    for item in all_of:
        if not isinstance(item, dict):
            continue
        if_cond = item.get("if")
        if not isinstance(if_cond, dict):
            continue
        props = if_cond.get("properties")
        if not isinstance(props, dict):
            continue
        operation = props.get("operation")
        if isinstance(operation, dict) and isinstance(
            operation.get("const"), str
        ):
            operations.add(operation["const"])
    assert operations == {
        "replace_section",
        "archive_section",
        "create",
        "append_history",
    }


def test_consolidator_retries_contract_violation_with_corrective_hint() -> None:
    gateway = FakeGateway(
        StructuredOutputError("model output failed schema validation"),
        _response(_valid_patches()),
    )
    consolidator = GatewayWikiConsolidator(gateway)  # type: ignore[arg-type]

    patches = asyncio.run(
        consolidator.consolidate(
            branch_id="main",
            subject_id=None,
            pages=(_state_page(),),
            sources=(),
            content_locale="en-US",
        )
    )

    assert len(patches) == 1
    assert patches[0].path == "world/state.md"
    assert len(gateway.calls) == 2
    assert "exists verbatim in the target page" in gateway.calls[1].messages[0].content


def test_consolidator_retries_when_section_is_missing_from_page() -> None:
    gateway = FakeGateway(
        _response(
            {
                "patches": [
                    {
                        "path": "world/state.md",
                        "operation": "replace_section",
                        "section": "Invented Heading",
                        "content": "The lighthouse is dark.",
                        "source_ids": ["source:0"],
                    }
                ]
            }
        ),
        _response(_valid_patches()),
    )
    consolidator = GatewayWikiConsolidator(gateway)  # type: ignore[arg-type]

    patches = asyncio.run(
        consolidator.consolidate(
            branch_id="main",
            subject_id=None,
            pages=(_state_page(),),
            sources=(),
            content_locale="en-US",
        )
    )

    assert len(patches) == 1
    assert len(gateway.calls) == 2
    assert "world/state.md: Current State" in gateway.calls[1].messages[0].content


def test_consolidator_fails_after_bounded_retries() -> None:
    gateway = FakeGateway(
        StructuredOutputError("model output failed schema validation"),
        StructuredOutputError("model output failed schema validation"),
    )
    consolidator = GatewayWikiConsolidator(gateway)  # type: ignore[arg-type]

    with pytest.raises(StructuredOutputError):
        asyncio.run(
            consolidator.consolidate(
                branch_id="main",
                subject_id=None,
                pages=(),
                sources=(),
                content_locale="en-US",
            )
        )

    assert len(gateway.calls) == MAX_CONSOLIDATION_ATTEMPTS


def test_consolidator_accepts_valid_patch_output() -> None:
    gateway = FakeGateway(_response(_valid_patches()))
    consolidator = GatewayWikiConsolidator(gateway)  # type: ignore[arg-type]

    patches = asyncio.run(
        consolidator.consolidate(
            branch_id="main",
            subject_id=None,
            pages=(_state_page(),),
            sources=(),
            content_locale="en-US",
        )
    )

    assert len(patches) == 1
    assert len(gateway.calls) == 1


def test_consolidator_does_not_expose_existing_page_source_ids() -> None:
    gateway = FakeGateway(_response(_valid_patches()))
    consolidator = GatewayWikiConsolidator(gateway)  # type: ignore[arg-type]
    page = _state_page().model_copy(update={"source_ids": ("event:old",)})
    source = WikiSource(
        source_id="source:0",
        kind=WikiSourceKind.EVENT,
        branch_id="main",
        step=1,
        content="The lighthouse is dark.",
    )

    asyncio.run(
        consolidator.consolidate(
            branch_id="main",
            subject_id=None,
            pages=(page,),
            sources=(source,),
            content_locale="en-US",
        )
    )

    prompt = gateway.calls[0].messages[0].content
    assert "event:old" not in prompt
    assert "source:0" in prompt
