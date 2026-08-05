import json
import re
import uuid
from typing import Protocol

from pydantic import ValidationError

from story_engine.domain.message import ModelMessageContext
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
WIKI_PROTOCOL = (
    "Immutable protocol: return WikiPatch objects only; every material statement "
    "must cite supplied source_ids; never modify raw sources, index.md, log.md, or "
    "SCHEMA.md; never treat Director Instructions as facts."
)


class WikiConsolidator(Protocol):
    async def consolidate(
        self,
        *,
        project_id: str,
        session_id: str | None,
        step: int,
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
    ) -> None:
        self.gateway = gateway

    async def consolidate(
        self,
        *,
        project_id: str,
        session_id: str | None,
        step: int,
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
        message_id = f"call:{uuid.uuid4().hex}"
        for attempt in range(MAX_CONSOLIDATION_ATTEMPTS):
            try:
                profile = self.gateway.registry.get_profile("wiki_maintainer")
                response = await self.gateway.complete(
                    ModelRequest(
                        profile_id="wiki_maintainer",
                        task_type="wiki_maintainer",
                        messages=(
                            Message(role="system", content=WIKI_PROTOCOL),
                            Message(
                                role="system",
                                content=profile.default_system_prompt,
                            ),
                            Message(
                                role="user",
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
                        max_output_tokens=profile.max_output_tokens,
                        output_token_limit=(
                            "provider"
                            if profile.max_output_tokens is None
                            else "profile"
                        ),
                        timeout_seconds=profile.timeout_seconds,
                        temperature=profile.temperature,
                        reasoning_effort=profile.reasoning_effort,
                    ),
                    context=ModelMessageContext(
                        project_id=project_id,
                        message_id=message_id,
                        agent_name=profile.name,
                        task_label=scope,
                        session_id=session_id,
                        branch_id=branch_id,
                        step=step,
                        stage="wiki",
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
