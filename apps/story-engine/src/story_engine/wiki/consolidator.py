import json
import re
import uuid
from typing import Protocol

from pydantic import ValidationError

from story_engine.domain.message import ModelMessageContext
from story_engine.domain.wiki import (
    WikiPage,
    WikiPatch,
    WikiPatchOperation,
    WikiSource,
    WikiUpdateProposal,
)
from story_engine.models.contracts import Message, ModelRequest
from story_engine.models.errors import StructuredOutputError
from story_engine.models.gateway import ModelGateway

MAX_CONSOLIDATION_ATTEMPTS = 2
WIKI_PROTOCOL = (
    "Immutable protocol: return WikiUpdateProposal only. Use only supplied page_ref "
    "and numeric source_refs. Do not invent pages or sources and do not treat "
    "Director Instructions as facts."
)


class WikiProtocolError(ValueError):
    """The model failed the bounded Wiki proposal protocol."""


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


def _page_refs(pages: tuple[WikiPage, ...]) -> dict[str, WikiPage]:
    result: dict[str, WikiPage] = {}
    for page in sorted(pages, key=lambda item: item.path):
        stem = re.sub(r"[^a-z0-9_]+", "_", page.path.rsplit("/", 1)[-1][:-3].lower())
        candidate = stem or "page"
        suffix = 2
        while candidate in result:
            candidate = f"{stem}_{suffix}"
            suffix += 1
        result[candidate] = page
    return result


class GatewayWikiConsolidator:
    """Ask the model for lightweight proposals and compile strict local patches."""

    def __init__(self, gateway: ModelGateway) -> None:
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
        pages_by_ref = _page_refs(pages)
        sources_by_ref = dict(enumerate(sources))
        last_error: Exception | None = None
        message_id = f"call:{uuid.uuid4().hex}"

        for attempt in range(MAX_CONSOLIDATION_ATTEMPTS):
            corrective_hint = None
            if attempt and last_error is not None:
                corrective_hint = (
                    "Your previous proposal was rejected. Correct this exact error: "
                    f"{last_error}. Allowed page_refs: {sorted(pages_by_ref)}. "
                    f"Allowed source_refs: {sorted(sources_by_ref)}."
                )
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
                                    scope=scope,
                                    pages_by_ref=pages_by_ref,
                                    sources_by_ref=sources_by_ref,
                                    content_locale=content_locale,
                                    corrective_hint=corrective_hint,
                                ),
                            ),
                        ),
                        output_schema=json.dumps(
                            WikiUpdateProposal.model_json_schema(),
                            ensure_ascii=False,
                        ),
                        structured_output_retry="caller",
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
                proposal = WikiUpdateProposal.model_validate(response.parsed_output)
                return self._compile(
                    proposal,
                    step=step,
                    pages_by_ref=pages_by_ref,
                    sources_by_ref=sources_by_ref,
                )
            except (StructuredOutputError, ValidationError, WikiProtocolError) as error:
                last_error = error

        detail = str(last_error or "no model response")
        raise WikiProtocolError(
            "Wiki proposal failed after "
            f"{MAX_CONSOLIDATION_ATTEMPTS} attempts: {detail}"
        ) from last_error

    @staticmethod
    def _compile(
        proposal: WikiUpdateProposal,
        *,
        step: int,
        pages_by_ref: dict[str, WikiPage],
        sources_by_ref: dict[int, WikiSource],
    ) -> tuple[WikiPatch, ...]:
        patches: list[WikiPatch] = []
        seen_pages: set[str] = set()
        for update in proposal.updates:
            page = pages_by_ref.get(update.page_ref)
            if page is None:
                raise WikiProtocolError(
                    f"unknown page_ref {update.page_ref!r}; "
                    f"allowed: {sorted(pages_by_ref)}"
                )
            unknown_refs = set(update.source_refs) - set(sources_by_ref)
            if unknown_refs:
                raise WikiProtocolError(
                    f"unknown source_refs {sorted(unknown_refs)}; "
                    f"allowed: {sorted(sources_by_ref)}"
                )
            if update.page_ref in seen_pages:
                raise WikiProtocolError(
                    f"duplicate update for page_ref {update.page_ref!r}"
                )
            seen_pages.add(update.page_ref)
            patches.append(
                WikiPatch(
                    path=page.path,
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content=f"## Step {step}\n\n{update.content.strip()}",
                    source_ids=tuple(
                        sources_by_ref[item].source_id for item in update.source_refs
                    ),
                    visibility=page.visibility,
                    proposal_page_ref=update.page_ref,
                    proposal_source_refs=update.source_refs,
                )
            )
        return tuple(patches)

    @staticmethod
    def _prompt(
        *,
        scope: str,
        pages_by_ref: dict[str, WikiPage],
        sources_by_ref: dict[int, WikiSource],
        content_locale: str,
        corrective_hint: str | None,
    ) -> str:
        prompt = (
            "Consolidate durable knowledge without inventing facts. For each changed "
            "page, return its page_ref, concise Markdown content to append, and one or "
            "more numeric source_refs. Omit unchanged pages. The store generates "
            "paths, "
            "operations, visibility, revisions, hashes, and real source IDs. "
            f"Respond in {content_locale}. Scope: {scope}. Editable pages: "
            + json.dumps(
                [
                    {"page_ref": page_ref, "content": page.content}
                    for page_ref, page in pages_by_ref.items()
                ],
                ensure_ascii=False,
            )
            + ". Sources: "
            + json.dumps(
                [
                    {
                        "source_ref": source_ref,
                        "kind": source.kind.value,
                        "content": source.content,
                    }
                    for source_ref, source in sources_by_ref.items()
                ],
                ensure_ascii=False,
            )
        )
        return f"{prompt}\n{corrective_hint}" if corrective_hint else prompt
