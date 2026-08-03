from pathlib import Path

from story_engine.domain.memory import MemoryRecord
from story_engine.wiki.store import WikiStore


class WikiContextBuilder:
    """Build bounded Wiki context for Actor, Game Master, and Writer consumers."""

    def __init__(
        self,
        root: Path,
        branch_id: str,
        *,
        max_context_chars: int = 32_768,
        recent_memory_limit: int = 8,
    ) -> None:
        if max_context_chars <= 0 or not 4 <= recent_memory_limit <= 8:
            raise ValueError("invalid Wiki context bounds")
        self.store = WikiStore(root, branch_id)
        self.max_context_chars = max_context_chars
        self.recent_memory_limit = recent_memory_limit

    def _render_pages(self, paths: tuple[str, ...], max_chars: int) -> str:
        output = ""
        for path in paths:
            try:
                page = self.store.load_page(path)
            except FileNotFoundError:
                continue
            section = f"<!-- {path} -->\n{page.content}\n"
            separator = "\n" if output else ""
            remaining = max_chars - len(output) - len(separator)
            if remaining <= 0:
                break
            output += separator + section[:remaining]
        return output.rstrip()

    @staticmethod
    def _render_recent(memories: tuple[MemoryRecord, ...], max_chars: int) -> str:
        if not memories or max_chars <= 0:
            return ""
        separator_chars = len(memories) - 1
        item_budget = max(1, (max_chars - separator_chars) // len(memories))
        rendered = "\n".join(
            f"- {record.text}"[:item_budget] for record in memories
        )
        return rendered[:max_chars].rstrip()

    def _world_paths(self) -> tuple[str, ...]:
        return tuple(
            page.path
            for page in self.store.list_pages()
            if page.path.startswith("world/")
        )

    def _character_paths(self, subject_id: str) -> tuple[str, ...]:
        prefix = f"characters/{subject_id}/"
        return tuple(
            page.path
            for page in self.store.list_pages()
            if page.path.startswith(prefix)
        )

    def world(self) -> str:
        return self._render_pages(self._world_paths(), self.max_context_chars)

    def character(self, subject_id: str) -> str:
        return self._render_pages(
            self._character_paths(subject_id),
            self.max_context_chars,
        )

    def actor(
        self,
        subject_id: str,
        recent_memories: tuple[MemoryRecord, ...],
    ) -> str:
        selected = recent_memories[-self.recent_memory_limit :]
        wiki_header = "Character Wiki:\n"
        recent_header = "\n\nCurrent scene and recent raw observations:\n"
        content_budget = max(
            0,
            self.max_context_chars - len(wiki_header) - len(recent_header),
        )
        wiki_budget = content_budget // 2
        recent_budget = content_budget - wiki_budget
        wiki = self._render_pages(
            self._character_paths(subject_id),
            wiki_budget,
        )
        recent = self._render_recent(selected, recent_budget)
        return (
            f"{wiki_header}{wiki}{recent_header}{recent}"
        )[: self.max_context_chars].rstrip()

    def writer(self, viewpoint_actor_id: str | None) -> str:
        if viewpoint_actor_id is None:
            return self.world()
        world_header = "World Wiki:\n"
        character_header = "\n\nViewpoint Wiki:\n"
        content_budget = max(
            0,
            self.max_context_chars - len(world_header) - len(character_header),
        )
        world_budget = content_budget // 2
        character_budget = content_budget - world_budget
        world = self._render_pages(self._world_paths(), world_budget)
        character = self._render_pages(
            self._character_paths(viewpoint_actor_id),
            character_budget,
        )
        return (
            f"{world_header}{world}{character_header}{character}"
        )[: self.max_context_chars].rstrip()
