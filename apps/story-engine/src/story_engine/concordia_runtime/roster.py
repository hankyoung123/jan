import json
from collections.abc import Mapping
from typing import Any, Protocol

from story_engine.domain.projection import ResolvedEvent

MAX_SCENE_ROSTER_SIZE = 4


class RosterModel(Protocol):
    def sample_json(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        temperature: float = 0.1,
    ) -> Mapping[str, Any]: ...


class ConcordiaRosterPlanner:
    """Ask the configured Game Master profile for a public roster decision."""

    def __init__(
        self,
        *,
        model: RosterModel,
        premise_text: str,
        content_locale: str,
    ) -> None:
        self._model = model
        self._premise_text = premise_text
        self._content_locale = content_locale

    def _select(
        self,
        *,
        instruction: str,
        candidates: Mapping[str, tuple[str, str]],
        context: str,
    ) -> tuple[str, ...]:
        if not candidates:
            raise ValueError("roster selection requires at least one active Agent")
        ids_by_name: dict[str, str] = {}
        for actor_id, (display_name, _) in candidates.items():
            normalized_name = display_name.strip().casefold()
            if normalized_name in ids_by_name:
                raise ValueError(
                    "active Agent display names must be unique for roster selection"
                )
            ids_by_name[normalized_name] = actor_id
        roster = "\n".join(
            f"- {display_name}: {summary}"
            for display_name, summary in candidates.values()
        )
        schema: dict[str, Any] = {
            "type": "object",
            "required": ["actor_names"],
            "properties": {
                "actor_names": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": min(MAX_SCENE_ROSTER_SIZE, len(candidates)),
                    "uniqueItems": True,
                    "items": {
                        "enum": [
                            display_name for display_name, _ in candidates.values()
                        ]
                    },
                }
            },
            "additionalProperties": False,
        }
        payload = self._model.sample_json(
            f"{instruction} Select between one and {MAX_SCENE_ROSTER_SIZE} active "
            "Agents. Base the decision on location, goals, continuity, and the "
            "scene premise. Return the required JSON only; do not provide "
            "reasoning.\n"
            f"Locale: {self._content_locale}\nPremise: {self._premise_text}\n"
            f"{context}\nCandidates:\n{roster}",
            schema,
            temperature=0.1,
        )
        selected = payload.get("actor_names") if isinstance(payload, dict) else None
        if not isinstance(selected, list):
            raise ValueError("Game Master roster output has no actor_names")
        if not all(isinstance(actor_name, str) for actor_name in selected):
            raise ValueError("Game Master roster contains a non-string actor name")
        selected_names = tuple(selected)
        if not selected_names:
            raise ValueError("Game Master must select at least one active Agent")
        if len(selected_names) > MAX_SCENE_ROSTER_SIZE:
            raise ValueError("Game Master selected too many active Agents")
        if len(selected_names) != len(set(selected_names)):
            raise ValueError("Game Master roster actor names must be unique")
        unknown = {
            actor_name
            for actor_name in selected_names
            if actor_name.strip().casefold() not in ids_by_name
        }
        if unknown:
            raise ValueError(
                f"Game Master selected unknown Agent names: {sorted(unknown)}"
            )
        return tuple(
            ids_by_name[actor_name.strip().casefold()] for actor_name in selected_names
        )

    def select_initial(
        self,
        candidates: Mapping[str, tuple[str, str]],
    ) -> tuple[str, ...]:
        return self._select(
            instruction="Select the opening scene roster.",
            candidates=candidates,
            context="No prior scene has completed.",
        )

    def select_next(
        self,
        candidates: Mapping[str, tuple[str, str]],
        *,
        current_roster: tuple[str, ...],
        scene_events: tuple[ResolvedEvent, ...],
    ) -> tuple[str, ...]:
        names_by_id = {
            actor_id: display_name for actor_id, (display_name, _) in candidates.items()
        }
        event_context = json.dumps(
            [event.event_text for event in scene_events],
            ensure_ascii=False,
        )
        current_names = [
            names_by_id[item] for item in current_roster if item in names_by_id
        ]
        return self._select(
            instruction="Select the roster for the next scene.",
            candidates=candidates,
            context=(
                "Current roster: "
                f"{json.dumps(current_names, ensure_ascii=False)}\n"
                f"Completed scene events: {event_context}"
            ),
        )
