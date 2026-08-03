from pathlib import Path

from concordia.components.agent import (  # type: ignore[import-untyped]
    action_spec_ignored,
)
from concordia.components.agent import (
    memory as memory_component,
)
from concordia.typing import entity_component  # type: ignore[import-untyped]

from story_engine.concordia_runtime.memory import ConcordiaMemoryCodec
from story_engine.wiki.context import WikiContextBuilder


class WikiKnowledgeContext(action_spec_ignored.ActionSpecIgnored):  # type: ignore[misc]
    """Read one character Wiki plus a bounded tail of private raw memory."""

    def __init__(
        self,
        *,
        project_root: str,
        branch_id: str,
        subject_id: str,
        limit: int = 8,
        memory_component_key: str = memory_component.DEFAULT_MEMORY_COMPONENT_KEY,
    ) -> None:
        if limit <= 0:
            raise ValueError("knowledge retrieval limit must be positive")
        super().__init__("Character Wiki and recent private observations")
        self._project_root = project_root
        self._branch_id = branch_id
        self._subject_id = subject_id
        self._limit = limit
        self._memory_component_key = memory_component_key

    def _make_pre_act_value(self) -> str:
        memory = self.get_entity().get_component(
            self._memory_component_key,
            type_=memory_component.Memory,
        )
        codec = ConcordiaMemoryCodec()
        memories = tuple(
            record
            for value in memory.retrieve_recent(limit=self._limit)
            if (record := codec.decode(value)) is not None
        )
        return WikiContextBuilder(
            Path(self._project_root),
            self._branch_id,
            recent_memory_limit=min(8, max(4, self._limit)),
        ).actor(self._subject_id, memories)

    def get_state(self) -> entity_component.ComponentState:
        return {
            "limit": self._limit,
            "memory_component_key": self._memory_component_key,
        }

    def set_state(self, state: entity_component.ComponentState) -> None:
        if "limit" in state:
            self._limit = int(state["limit"])
        if "memory_component_key" in state:
            self._memory_component_key = str(state["memory_component_key"])


class WorldWikiContext(action_spec_ignored.ActionSpecIgnored):  # type: ignore[misc]
    """Read the bounded World Wiki owned by the current runtime branch."""

    def __init__(self, *, project_root: str, branch_id: str) -> None:
        super().__init__("World Wiki")
        self._project_root = project_root
        self._branch_id = branch_id

    def _make_pre_act_value(self) -> str:
        return WikiContextBuilder(Path(self._project_root), self._branch_id).world()

    def get_state(self) -> entity_component.ComponentState:
        return {}

    def set_state(self, state: entity_component.ComponentState) -> None:
        del state
