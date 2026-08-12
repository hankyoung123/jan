import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from story_engine.domain.wiki import (
    WikiContextBundle,
    WikiContextManifestEntry,
    WikiPage,
)
from story_engine.wiki.store import WikiStore

_LINK = re.compile(r"\[[^\]]+\]\(([^)]+\.md)(?:#[^)]*)?\)")
_WORD = re.compile(r"[\w\-]+", re.UNICODE)


class WikiContextUnavailableError(ValueError):
    """Raised when the working Wiki is not the current branch projection."""


@dataclass(frozen=True, slots=True)
class _Candidate:
    page: WikiPage
    priority: int
    reason: str
    permission: str


class WikiContextBuilder:
    """Select bounded Wiki context through deterministic scene-aware routing."""

    def __init__(
        self,
        root: Path,
        branch_id: str,
        *,
        max_context_chars: int = 32_768,
        version_id: str | None = None,
        excluded_source_ids: frozenset[str] = frozenset(),
        require_current_head: bool = False,
    ) -> None:
        if max_context_chars <= 0:
            raise ValueError("invalid Wiki context bounds")
        self.store = WikiStore(root, branch_id)
        self.root = root
        self.branch_id = branch_id
        self.max_context_chars = max_context_chars
        self.version_id = version_id
        self.excluded_source_ids = excluded_source_ids
        self.require_current_head = require_current_head
        if version_id is not None and require_current_head:
            raise ValueError("historical Wiki context cannot require the current head")

    def _assert_current_head(self) -> None:
        if not self.require_current_head:
            return
        if not self.store.is_reachable_projection():
            raise WikiContextUnavailableError(
                "working Wiki is stale, degraded, or outside branch lineage"
            )

    def _pages(
        self,
        *,
        viewpoint_actor_id: str | None = None,
        include_private: bool = False,
    ) -> tuple[WikiPage, ...]:
        self._assert_current_head()
        pages = (
            self.store.list_pages()
            if self.version_id is None
            else self.store.list_version_pages(self.version_id)
        )
        return tuple(
            page
            for page in pages
            if not self.excluded_source_ids.intersection(page.source_ids)
            and (
                include_private
                or page.visibility == "public"
                or page.visibility == f"private:{viewpoint_actor_id}"
            )
        )

    @staticmethod
    def _tokens(text: str) -> int:
        return (len(text) + 3) // 4

    @staticmethod
    def _terms(values: tuple[str, ...]) -> set[str]:
        return {
            term.casefold()
            for value in values
            for term in _WORD.findall(value)
            if len(term) > 1
        }

    @staticmethod
    def _resolve_link(source_path: str, target: str) -> str | None:
        parts: list[str] = []
        for part in (PurePosixPath(source_path).parent / target).parts:
            if part in {"", "."}:
                continue
            if part == "..":
                if not parts:
                    return None
                parts.pop()
            else:
                parts.append(part)
        return PurePosixPath(*parts).as_posix()

    def _linked_paths(self, pages: tuple[WikiPage, ...]) -> set[str]:
        linked: set[str] = set()
        for page in pages:
            if PurePosixPath(page.path).name != "index.md":
                continue
            for target in _LINK.findall(page.content):
                resolved = self._resolve_link(page.path, target)
                if resolved is not None:
                    linked.add(resolved)
        return linked

    def _candidates(
        self,
        *,
        prefix: str,
        permission: str,
        participant_ids: tuple[str, ...] = (),
        location_ids: tuple[str, ...] = (),
        entity_ids: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
        viewpoint_actor_id: str | None = None,
        include_private: bool = False,
    ) -> tuple[_Candidate, ...]:
        pages = tuple(
            page
            for page in self._pages(
                viewpoint_actor_id=viewpoint_actor_id,
                include_private=include_private,
            )
            if page.path.startswith(prefix)
        )
        linked = self._linked_paths(pages)
        scene_terms = self._terms(
            (*participant_ids, *location_ids, *entity_ids, *keywords)
        )
        candidates: list[_Candidate] = []
        for page in pages:
            name = PurePosixPath(page.path).stem.casefold()
            searchable = f"{page.path}\n{page.content}".casefold()
            page_terms = self._terms((searchable,))
            if name in {"index", "profile", "self", "goals", "plans", "state"}:
                priority, reason = 0, f"mandatory_{name}"
            elif (
                "relationship" in searchable
                and self._terms(participant_ids) & page_terms
            ):
                priority, reason = 1, "current_participant_relationship"
            elif self._terms(location_ids) & page_terms:
                priority, reason = 2, "current_location"
            elif self._terms(entity_ids) & page_terms:
                priority, reason = 2, "current_entity"
            elif page.path in linked:
                priority, reason = 3, "explicit_index_link"
            elif scene_terms & page_terms:
                priority, reason = 4, "keyword_match"
            elif page.confidence >= 0.8:
                priority, reason = 5, "high_importance"
            else:
                priority, reason = 6, "recent_update"
            candidates.append(_Candidate(page, priority, reason, permission))
        candidates.sort(
            key=lambda item: (
                item.priority,
                -item.page.updated_at_step,
                -item.page.confidence,
                item.page.path,
            )
        )
        return tuple(candidates)

    def _render(
        self,
        candidates: tuple[_Candidate, ...],
        max_chars: int,
    ) -> WikiContextBundle:
        if max_chars <= 0:
            return WikiContextBundle(content="")
        output = ""
        manifest: list[WikiContextManifestEntry] = []
        page_cap = max(256, max_chars // max(1, min(8, len(candidates))))
        for candidate in candidates:
            header = f"<!-- {candidate.page.path} -->\n"
            section = f"{header}{candidate.page.content}\n"
            separator = "\n" if output else ""
            remaining = max_chars - len(output) - len(separator)
            if remaining <= len(header):
                break
            included = section[: min(remaining, page_cap)].rstrip()
            output += separator + included
            manifest.append(
                WikiContextManifestEntry(
                    path=candidate.page.path,
                    reason=candidate.reason,
                    permission=candidate.permission,
                    source_ids=candidate.page.source_ids,
                    estimated_tokens=self._tokens(included),
                )
            )
        return WikiContextBundle(content=output.rstrip(), manifest=tuple(manifest))

    def world(
        self,
        *,
        participant_ids: tuple[str, ...] = (),
        location_ids: tuple[str, ...] = (),
        entity_ids: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
    ) -> WikiContextBundle:
        return self._render(
            self._candidates(
                prefix="world/",
                permission="world",
                participant_ids=participant_ids,
                location_ids=location_ids,
                entity_ids=entity_ids,
                keywords=keywords,
                viewpoint_actor_id=None,
            ),
            self.max_context_chars,
        )

    def character(
        self,
        subject_id: str,
        *,
        participant_ids: tuple[str, ...] = (),
        location_ids: tuple[str, ...] = (),
        entity_ids: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
    ) -> WikiContextBundle:
        return self._render(
            self._candidates(
                prefix=f"characters/{subject_id}/",
                permission=f"private:{subject_id}",
                participant_ids=participant_ids,
                location_ids=location_ids,
                entity_ids=entity_ids,
                keywords=keywords,
                viewpoint_actor_id=subject_id,
            ),
            self.max_context_chars,
        )

    def writer(
        self,
        viewpoint_actor_id: str | None,
        *,
        participant_ids: tuple[str, ...] = (),
        location_ids: tuple[str, ...] = (),
        entity_ids: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
    ) -> WikiContextBundle:
        if viewpoint_actor_id is None:
            return self.world(
                participant_ids=participant_ids,
                location_ids=location_ids,
                entity_ids=entity_ids,
                keywords=keywords,
            )
        world_header = "World Wiki:\n"
        character_header = "\n\nViewpoint Wiki:\n"
        content_budget = max(
            0,
            self.max_context_chars - len(world_header) - len(character_header),
        )
        world = self._render(
            self._candidates(
                prefix="world/",
                permission="world",
                participant_ids=participant_ids,
                location_ids=location_ids,
                entity_ids=entity_ids,
                keywords=keywords,
                viewpoint_actor_id=viewpoint_actor_id,
            ),
            content_budget // 2,
        )
        character = self._render(
            self._candidates(
                prefix=f"characters/{viewpoint_actor_id}/",
                permission=f"private:{viewpoint_actor_id}",
                participant_ids=participant_ids,
                location_ids=location_ids,
                entity_ids=entity_ids,
                keywords=keywords,
                viewpoint_actor_id=viewpoint_actor_id,
            ),
            content_budget - len(world.content),
        )
        content = (
            f"{world_header}{world.content}{character_header}{character.content}"
        )
        return WikiContextBundle(
            content=content[: self.max_context_chars].rstrip(),
            manifest=(*world.manifest, *character.manifest),
        )

    def editor(
        self,
        *,
        participant_ids: tuple[str, ...] = (),
        location_ids: tuple[str, ...] = (),
        entity_ids: tuple[str, ...] = (),
        keywords: tuple[str, ...] = (),
    ) -> WikiContextBundle:
        return self._render(
            self._candidates(
                prefix="",
                permission="editor_fact_check",
                participant_ids=participant_ids,
                location_ids=location_ids,
                entity_ids=entity_ids,
                keywords=keywords,
                include_private=True,
            ),
            self.max_context_chars,
        )
