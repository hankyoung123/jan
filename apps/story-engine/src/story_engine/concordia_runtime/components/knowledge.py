import json
from pathlib import Path

from concordia.components.agent import (  # type: ignore[import-untyped]
    action_spec_ignored,
)
from concordia.components.agent import (
    memory as memory_component,
)
from concordia.typing import entity as entity_lib  # type: ignore[import-untyped]
from concordia.typing import entity_component  # type: ignore[import-untyped]

from story_engine.concordia_runtime.memory import (
    ConcordiaMemoryCodec,
    rank_memory_records,
)
from story_engine.domain.memory import MemoryQuery, MemoryRecord, MemoryRecordType
from story_engine.wiki.context import WikiContextBuilder

RECENT_MEMORY_LIMIT = 12
RELEVANT_MEMORY_LIMIT = 6
_RECENT_CONTEXT_CHARS = 12_000
_RELEVANT_CONTEXT_CHARS = 6_000
_PERCEPTION_CONTEXT_CHARS = 4_000


def _records(
    entity: entity_component.EntityWithComponents,
    memory_component_key: str,
) -> tuple[MemoryRecord, ...]:
    memory = entity.get_component(
        memory_component_key,
        type_=memory_component.AssociativeMemory,
    )
    codec = ConcordiaMemoryCodec()
    return tuple(
        record
        for value in memory.get_all_memories_as_text()
        if (record := codec.decode(value)) is not None
    )


def _observations(records: tuple[MemoryRecord, ...]) -> tuple[MemoryRecord, ...]:
    return tuple(
        record
        for record in records
        if record.record_type == MemoryRecordType.OBSERVATION
    )


def _render_records(records: tuple[MemoryRecord, ...], *, max_chars: int) -> str:
    output = ""
    for record in records:
        line = f"- [Step {record.step}] {record.text.strip()}"
        remaining = max_chars - len(output) - (1 if output else 0)
        if remaining <= 0:
            break
        output += ("\n" if output else "") + line[:remaining].rstrip()
    return output


class WikiKnowledgeContext(action_spec_ignored.ActionSpecIgnored):  # type: ignore[misc]
    """Read only the long-term semantic Wiki for one character."""

    def __init__(
        self,
        *,
        project_root: str,
        branch_id: str,
        subject_id: str,
    ) -> None:
        super().__init__("Wiki")
        self._project_root = project_root
        self._branch_id = branch_id
        self._subject_id = subject_id

    def _make_pre_act_value(self) -> str:
        return WikiContextBuilder(
            Path(self._project_root),
            self._branch_id,
        ).character(self._subject_id).content

    def get_state(self) -> entity_component.ComponentState:
        return {}

    def set_state(self, state: entity_component.ComponentState) -> None:
        del state


class RecentMemoryContext(action_spec_ignored.ActionSpecIgnored):  # type: ignore[misc]
    """Render the twelve observations immediately before current perception."""

    def __init__(
        self,
        *,
        limit: int = RECENT_MEMORY_LIMIT,
        memory_component_key: str = memory_component.DEFAULT_MEMORY_COMPONENT_KEY,
    ) -> None:
        super().__init__("Recent Memory")
        self._limit = limit
        self._memory_component_key = memory_component_key

    def _make_pre_act_value(self) -> str:
        observations = _observations(
            _records(self.get_entity(), self._memory_component_key)
        )
        recent = observations[-(self._limit + 1) : -1]
        return _render_records(recent, max_chars=_RECENT_CONTEXT_CHARS)

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


class CurrentPerceptionContext(action_spec_ignored.ActionSpecIgnored):  # type: ignore[misc]
    """Render only the actor's current perception."""

    def __init__(
        self,
        *,
        memory_component_key: str = memory_component.DEFAULT_MEMORY_COMPONENT_KEY,
    ) -> None:
        super().__init__("Current Perception")
        self._memory_component_key = memory_component_key

    def _make_pre_act_value(self) -> str:
        observations = _observations(
            _records(self.get_entity(), self._memory_component_key)
        )
        if not observations:
            return ""
        return observations[-1].text[:_PERCEPTION_CONTEXT_CHARS].rstrip()

    def get_state(self) -> entity_component.ComponentState:
        return {"memory_component_key": self._memory_component_key}

    def set_state(self, state: entity_component.ComponentState) -> None:
        if "memory_component_key" in state:
            self._memory_component_key = str(state["memory_component_key"])


class RelevantMemoryContext(entity_component.ContextComponent):
    """Recall six older private memories with no model or external index."""

    def __init__(
        self,
        *,
        subject_id: str,
        limit: int = RELEVANT_MEMORY_LIMIT,
        recent_limit: int = RECENT_MEMORY_LIMIT,
        actor_state_component_key: str = "actor_state",
        memory_component_key: str = memory_component.DEFAULT_MEMORY_COMPONENT_KEY,
    ) -> None:
        super().__init__()
        self._subject_id = subject_id
        self._limit = limit
        self._recent_limit = recent_limit
        self._actor_state_component_key = actor_state_component_key
        self._memory_component_key = memory_component_key

    def _actor_state(self) -> dict[str, object]:
        component = self.get_entity().get_component(
            self._actor_state_component_key,
            type_=action_spec_ignored.ActionSpecIgnored,
        )
        try:
            value = json.loads(component.get_pre_act_value())
        except (TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        records = _records(self.get_entity(), self._memory_component_key)
        observations = _observations(records)
        current = observations[-1] if observations else None
        recent = observations[-(self._recent_limit + 1) : -1]
        excluded_ids = {record.record_id for record in recent}
        if current is not None:
            excluded_ids.add(current.record_id)
        actor_state = self._actor_state()
        participants = tuple(
            actor_id
            for actor_id in (current.actor_ids if current is not None else ())
            if actor_id != self._subject_id
        )
        location_ids = current.location_ids if current is not None else ()
        tags = tuple(
            dict.fromkeys(
                (
                    *(
                        tag
                        for tag in (current.tags if current is not None else ())
                        if tag != "observation"
                    ),
                    *((action_spec.tag,) if action_spec.tag else ()),
                )
            )
        )
        query_parts = (
            current.text if current is not None else "",
            str(actor_state.get("current_goal") or ""),
            str(actor_state.get("location") or ""),
            " ".join(participants),
            action_spec.call_to_action,
            " ".join(action_spec.options),
            action_spec.tag or "",
        )
        query_text = "\n".join(part for part in query_parts if part).strip()
        if not query_text:
            return ""
        candidates = tuple(
            record for record in records if record.record_id not in excluded_ids
        )
        hits = rank_memory_records(
            candidates,
            MemoryQuery(
                query_text=query_text[:32_768],
                limit=self._limit,
                actor_ids=participants,
                location_ids=location_ids,
                tags=tags,
                before_step=current.step if current is not None else None,
                content_locale=(
                    current.content_locale if current is not None else None
                ),
            ),
        )
        value = _render_records(
            tuple(hit.record for hit in hits),
            max_chars=_RELEVANT_CONTEXT_CHARS,
        )
        return f"Relevant Recall:\n{value}\n" if value else ""

    def get_state(self) -> entity_component.ComponentState:
        return {
            "limit": self._limit,
            "recent_limit": self._recent_limit,
            "actor_state_component_key": self._actor_state_component_key,
            "memory_component_key": self._memory_component_key,
        }

    def set_state(self, state: entity_component.ComponentState) -> None:
        if "limit" in state:
            self._limit = int(state["limit"])
        if "recent_limit" in state:
            self._recent_limit = int(state["recent_limit"])
        if "actor_state_component_key" in state:
            self._actor_state_component_key = str(
                state["actor_state_component_key"]
            )
        if "memory_component_key" in state:
            self._memory_component_key = str(state["memory_component_key"])


class WorldWikiContext(action_spec_ignored.ActionSpecIgnored):  # type: ignore[misc]
    """Read the bounded World Wiki owned by the current runtime branch."""

    def __init__(self, *, project_root: str, branch_id: str) -> None:
        super().__init__("World Wiki")
        self._project_root = project_root
        self._branch_id = branch_id

    def _make_pre_act_value(self) -> str:
        return WikiContextBuilder(
            Path(self._project_root), self._branch_id
        ).world().content

    def get_state(self) -> entity_component.ComponentState:
        return {}

    def set_state(self, state: entity_component.ComponentState) -> None:
        del state
