import json
import re
from typing import Protocol

from pydantic import ValidationError

from story_engine.domain.wiki import (
    WikiConsolidationOutput,
    WikiPage,
    WikiPatch,
    WikiPatchOperation,
    WikiSource,
)
from story_engine.models.contracts import Message, ModelRequest
from story_engine.models.errors import StructuredOutputError
from story_engine.models.gateway import ModelGateway

MAX_CONSOLIDATION_ATTEMPTS = 2
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


class WikiConsolidator(Protocol):
    async def consolidate(
        self,
        *,
        branch_id: str,
        subject_id: str | None,
        pages: tuple[WikiPage, ...],
        sources: tuple[WikiSource, ...],
        content_locale: str,
    ) -> tuple[WikiPatch, ...]: ...


class GatewayWikiConsolidator:
    """Ask the Jan-backed model for patches; never grant direct file access."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        profile_id: str = "wiki-maintenance",
    ) -> None:
        self.gateway = gateway
        self.profile_id = profile_id

    async def consolidate(
        self,
        *,
        branch_id: str,
        subject_id: str | None,
        pages: tuple[WikiPage, ...],
        sources: tuple[WikiSource, ...],
        content_locale: str,
    ) -> tuple[WikiPatch, ...]:
        scope = "World Wiki" if subject_id is None else f"Character Wiki: {subject_id}"
        last_error: Exception | None = None
        heading_map = "\n".join(
            f"{page.path}: "
            + ", ".join(
                sorted(
                    {
                        match.group(2).strip()
                        for match in _HEADING.finditer(page.content)
                    }
                )
            )
            for page in pages
        )
        for attempt in range(MAX_CONSOLIDATION_ATTEMPTS):
            try:
                response = await self.gateway.complete(
                    ModelRequest(
                        profile_id=self.profile_id,
                        task_type="wiki_maintenance",
                        messages=(
                            Message(
                                role="system",
                                content=self._prompt(
                                    branch_id=branch_id,
                                    scope=scope,
                                    pages=pages,
                                    sources=sources,
                                    content_locale=content_locale,
                                    corrective_hint=(
                                        (
                                            "Your previous attempt violated the "
                                            "WikiPatch contract. replace_section and "
                                            "archive_section must use a section "
                                            "heading that exists verbatim in the "
                                            "target page; create and append_history "
                                            "must not include a section field at "
                                            "all. Exact available headings:\n"
                                            + (heading_map or "(no pages)")
                                        )
                                        if attempt > 0
                                        else None
                                    ),
                                ),
                            ),
                        ),
                        output_schema=json.dumps(
                            _wiki_output_schema(),
                            ensure_ascii=False,
                        ),
                        max_output_tokens=4096,
                        timeout_seconds=120,
                        temperature=0.1,
                    ),
                )
            except StructuredOutputError as error:
                last_error = error
                continue
            try:
                patches = WikiConsolidationOutput.model_validate(
                    response.parsed_output
                ).patches
            except ValidationError as error:
                last_error = error
                continue
            section_errors = _section_errors(
                patches,
                {page.path: page for page in pages},
            )
            if section_errors:
                last_error = ValueError("; ".join(section_errors))
                continue
            return patches
        if last_error is not None:
            raise last_error
        raise RuntimeError("wiki consolidation failed without a model response")

    @staticmethod
    def _prompt(
        *,
        branch_id: str,
        scope: str,
        pages: tuple[WikiPage, ...],
        sources: tuple[WikiSource, ...],
        content_locale: str,
        corrective_hint: str | None,
    ) -> str:
        prompt = (
            "You are the disciplined Story Engine Wiki maintainer. Return WikiPatch "
            "objects only. Consolidate durable knowledge without inventing facts. "
            "Every material statement must cite one or more supplied source_ids. "
            "Preserve changes in understanding with append_history or an explicitly "
            "labelled belief/suspected/uncertain statement. Never modify runtime raw "
            "sources, index.md, log.md, or SCHEMA.md. Create entity pages only when "
            "the supplied sources contain real entity knowledge. Keep every page "
            "compact. "
            f"Respond in {content_locale}. Branch: {branch_id}. Scope: {scope}. "
            "Existing pages: "
            + json.dumps(
                [
                    {
                        "path": page.path,
                        "content": page.content,
                    }
                    for page in pages
                ],
                ensure_ascii=False,
            )
            + ". Allowed raw sources: "
            + json.dumps(
                [source.model_dump(mode="json") for source in sources],
                ensure_ascii=False,
            )
        )
        if corrective_hint is not None:
            prompt = f"{prompt}\n{corrective_hint}"
        return prompt


def _section_errors(
    patches: tuple[WikiPatch, ...],
    pages_by_path: dict[str, WikiPage],
) -> list[str]:
    """Report replace/archive patches whose section is missing in the page."""

    errors: list[str] = []
    for patch in patches:
        if patch.operation not in {
            WikiPatchOperation.REPLACE_SECTION,
            WikiPatchOperation.ARCHIVE_SECTION,
        }:
            continue
        page = pages_by_path.get(patch.path)
        if page is None:
            errors.append(f"{patch.path}: target page is missing")
            continue
        headings = {
            match.group(2).strip() for match in _HEADING.finditer(page.content)
        }
        if patch.section not in headings:
            errors.append(
                f"{patch.path}: section {patch.section!r} does not exist; "
                f"available headings: {sorted(headings)}"
            )
    return errors


def _wiki_output_schema() -> dict[str, object]:
    """Strict output schema tying each operation to its section contract."""

    schema = WikiConsolidationOutput.model_json_schema()
    patch_schema = schema["properties"]["patches"]["items"]
    if not isinstance(patch_schema, dict):
        raise RuntimeError("wiki output schema is invalid")
    section_contract = [
        {
            "if": {
                "properties": {
                    "operation": {"const": "replace_section"}
                },
                "required": ["operation"],
            },
            "then": {
                "required": ["section"],
                "properties": {"section": {"type": "string", "minLength": 1}},
            },
        },
        {
            "if": {
                "properties": {
                    "operation": {"const": "archive_section"}
                },
                "required": ["operation"],
            },
            "then": {
                "required": ["section"],
                "properties": {"section": {"type": "string", "minLength": 1}},
            },
        },
        {
            "if": {
                "properties": {"operation": {"const": "create"}},
                "required": ["operation"],
            },
            "then": {
                "not": {
                    "properties": {"section": {"type": "string"}},
                    "required": ["section"],
                }
            },
        },
        {
            "if": {
                "properties": {
                    "operation": {"const": "append_history"}
                },
                "required": ["operation"],
            },
            "then": {
                "not": {
                    "properties": {"section": {"type": "string"}},
                    "required": ["section"],
                }
            },
        },
    ]
    existing = patch_schema.get("allOf")
    if isinstance(existing, list):
        patch_schema["allOf"] = [*existing, *section_contract]
    else:
        patch_schema["allOf"] = section_contract
    return schema
