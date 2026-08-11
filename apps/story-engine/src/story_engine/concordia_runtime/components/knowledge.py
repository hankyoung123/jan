import json
from dataclasses import dataclass
from pathlib import Path

from concordia.components.agent import (  # type: ignore[import-untyped]
    action_spec_ignored,
)
from concordia.components.agent import (
    memory as memory_component,
)
from concordia.typing import entity as entity_lib  # type: ignore[import-untyped]
from concordia.typing import entity_component

from story_engine.concordia_runtime.memory import (
    ConcordiaMemoryCodec,
    rank_memory_records,
)
from story_engine.domain.memory import (
    MemoryHit,
    MemoryQuery,
    MemoryRecord,
    MemoryRecordType,
)
from story_engine.wiki.context import WikiContextBuilder

RECENT_MEMORY_LIMIT = 12
RELEVANT_MEMORY_LIMIT = 6


@dataclass(frozen=True, slots=True)
class CharacterContextBudget:
    """The single owner of all dynamic character-context allocations."""

    total_chars: int = 30_000
    wiki_chars: int = 12_000
    recent_chars: int = 8_000
    relevant_chars: int = 6_000


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


def _render_recent(records: tuple[MemoryRecord, ...], *, max_chars: int) -> str:
    """Keep newest records when the recent allocation must shrink."""

    selected: list[MemoryRecord] = []
    used = 0
    for record in reversed(records):
        line_chars = len(f"- [Step {record.step}] {record.text.strip()}")
        separator = 1 if selected else 0
        if used + separator + line_chars > max_chars:
            if not selected and max_chars > 0:
                selected.append(record)
            break
        selected.append(record)
        used += separator + line_chars
    return _render_records(tuple(reversed(selected)), max_chars=max_chars)


class CharacterContext(entity_component.ContextComponent):  # type: ignore[misc]
    """Build Wiki, recent, relevant, and perception under one total budget."""

    def __init__(
        self,
        *,
        project_root: str,
        branch_id: str,
        subject_id: str,
        budget: CharacterContextBudget | None = None,
        recent_limit: int = RECENT_MEMORY_LIMIT,
        relevant_limit: int = RELEVANT_MEMORY_LIMIT,
        actor_state_component_key: str = "actor_state",
        memory_component_key: str = memory_component.DEFAULT_MEMORY_COMPONENT_KEY,
    ) -> None:
        super().__init__()
        self._project_root = project_root
        self._branch_id = branch_id
        self._subject_id = subject_id
        self._budget = budget or CharacterContextBudget()
        self._recent_limit = recent_limit
        self._relevant_limit = relevant_limit
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
        # Relevant recall is the sole historical scan. Recent and current reuse
        # the same decoded projection instead of initiating their own scans.
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
        hits: tuple[MemoryHit, ...] = ()
        if query_text:
            candidates = tuple(
                record for record in records if record.record_id not in excluded_ids
            )
            hits = rank_memory_records(
                candidates,
                MemoryQuery(
                    query_text=query_text[:32_768],
                    limit=self._relevant_limit,
                    actor_ids=participants,
                    location_ids=location_ids,
                    tags=tags,
                    before_step=current.step if current is not None else None,
                    content_locale=(
                        current.content_locale if current is not None else None
                    ),
                ),
            )

        headings = (
            "Wiki:\n",
            "\nRecent Memory:\n",
            "\nRelevant Recall:\n",
            "\nCurrent Perception:\n",
        )
        current_text = current.text if current is not None else ""
        remaining = max(
            0,
            self._budget.total_chars
            - sum(len(heading) for heading in headings)
            - len(current_text),
        )

        relevant_budget = min(self._budget.relevant_chars, remaining)
        relevant = _render_records(
            tuple(hit.record for hit in hits),
            max_chars=relevant_budget,
        )
        remaining -= len(relevant)

        recent_budget = min(self._budget.recent_chars, remaining)
        recent_text = _render_recent(recent, max_chars=recent_budget)
        remaining -= len(recent_text)

        wiki_budget = min(self._budget.wiki_chars, remaining)
        wiki = (
            WikiContextBuilder(
                Path(self._project_root),
                self._branch_id,
                max_context_chars=wiki_budget,
            ).character(
                self._subject_id,
                participant_ids=participants,
                location_ids=location_ids,
            ).content
            if wiki_budget > 0
            else ""
        )
        return (
            f"{headings[0]}{wiki}"
            f"{headings[1]}{recent_text}"
            f"{headings[2]}{relevant}"
            f"{headings[3]}{current_text}"
        )

    def get_state(self) -> entity_component.ComponentState:
        return {
            "recent_limit": self._recent_limit,
            "relevant_limit": self._relevant_limit,
            "actor_state_component_key": self._actor_state_component_key,
            "memory_component_key": self._memory_component_key,
        }

    def set_state(self, state: entity_component.ComponentState) -> None:
        if "recent_limit" in state:
            self._recent_limit = int(state["recent_limit"])
        if "relevant_limit" in state:
            self._relevant_limit = int(state["relevant_limit"])
        if "actor_state_component_key" in state:
            self._actor_state_component_key = str(state["actor_state_component_key"])
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
