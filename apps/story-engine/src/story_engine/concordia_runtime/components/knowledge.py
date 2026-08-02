from concordia.components.agent import (  # type: ignore[import-untyped]
    action_spec_ignored,
)
from concordia.components.agent import (
    memory as memory_component,
)
from concordia.typing import entity_component  # type: ignore[import-untyped]


class KnowledgeContext(action_spec_ignored.ActionSpecIgnored):  # type: ignore[misc]
    """Retrieve only the memories attached to this entity."""

    def __init__(
        self,
        *,
        limit: int = 8,
        memory_component_key: str = memory_component.DEFAULT_MEMORY_COMPONENT_KEY,
    ) -> None:
        if limit <= 0:
            raise ValueError("knowledge retrieval limit must be positive")
        super().__init__("Relevant private memories")
        self._limit = limit
        self._memory_component_key = memory_component_key

    def _make_pre_act_value(self) -> str:
        memory = self.get_entity().get_component(
            self._memory_component_key,
            type_=memory_component.Memory,
        )
        memories = memory.retrieve_recent(limit=self._limit)
        return "\n".join(memories)

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
