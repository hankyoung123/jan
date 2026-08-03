import json
from typing import Protocol

from story_engine.domain.wiki import (
    WikiConsolidationOutput,
    WikiPage,
    WikiPatch,
    WikiSource,
)
from story_engine.models.contracts import Message, ModelRequest
from story_engine.models.gateway import ModelGateway


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
                        "source_ids": page.source_ids,
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
        response = await self.gateway.complete(
            ModelRequest(
                profile_id=self.profile_id,
                task_type="wiki_maintenance",
                messages=(Message(role="system", content=prompt),),
                output_schema=json.dumps(
                    WikiConsolidationOutput.model_json_schema(),
                    ensure_ascii=False,
                ),
                max_output_tokens=4096,
                timeout_seconds=120,
                temperature=0.1,
            )
        )
        return WikiConsolidationOutput.model_validate(response.parsed_output).patches
