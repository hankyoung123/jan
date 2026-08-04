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
        candidates: Mapping[str, str],
        context: str,
    ) -> tuple[str, ...]:
        if not candidates:
            raise ValueError("roster selection requires at least one active Agent")
        roster = "\n".join(
            f"- {actor_id}: {summary}" for actor_id, summary in candidates.items()
        )
        schema: dict[str, Any] = {
            "type": "object",
            "required": ["actor_ids"],
            "properties": {
                "actor_ids": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": min(MAX_SCENE_ROSTER_SIZE, len(candidates)),
                    "uniqueItems": True,
                    "items": {"enum": list(candidates)},
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
        selected = payload.get("actor_ids") if isinstance(payload, dict) else None
        if not isinstance(selected, list):
            raise ValueError("Game Master roster output has no actor_ids")
        if not all(isinstance(actor_id, str) for actor_id in selected):
            raise ValueError("Game Master roster contains a non-string actor ID")
        selected_ids = tuple(selected)
        if not selected_ids:
            raise ValueError("Game Master must select at least one active Agent")
        if len(selected_ids) > MAX_SCENE_ROSTER_SIZE:
            raise ValueError("Game Master selected too many active Agents")
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("Game Master roster actor IDs must be unique")
        unknown = set(selected_ids) - set(candidates)
        if unknown:
            raise ValueError(f"Game Master selected unknown Agents: {sorted(unknown)}")
        return selected_ids

    def select_initial(self, candidates: Mapping[str, str]) -> tuple[str, ...]:
        return self._select(
            instruction="Select the opening scene roster.",
            candidates=candidates,
            context="No prior scene has completed.",
        )

    def select_next(
        self,
        candidates: Mapping[str, str],
        *,
        current_roster: tuple[str, ...],
        scene_events: tuple[ResolvedEvent, ...],
    ) -> tuple[str, ...]:
        event_context = json.dumps(
            [
                {
                    "event_id": event.event_id,
                    "event_text": event.event_text,
                    "participant_ids": event.participant_ids,
                }
                for event in scene_events
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        return self._select(
            instruction="Select the roster for the next scene.",
            candidates=candidates,
            context=(
                f"Current roster: {json.dumps(current_roster)}\n"
                f"Completed scene events: {event_context}"
            ),
        )
