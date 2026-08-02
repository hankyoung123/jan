import json
from collections.abc import Mapping

from story_engine.concordia_runtime.language_model import JanConcordiaLanguageModel


class ConcordiaRosterPlanner:
    """Ask the configured Game Master profile for a public roster decision."""

    def __init__(
        self,
        *,
        model: JanConcordiaLanguageModel,
        premise_text: str,
        candidates: Mapping[str, str],
        content_locale: str,
    ) -> None:
        self._model = model
        self._premise_text = premise_text
        self._candidates = dict(candidates)
        self._content_locale = content_locale

    def select_initial(self) -> tuple[str, ...]:
        roster = "\n".join(
            f"- {actor_id}: {summary}" for actor_id, summary in self._candidates.items()
        )
        raw = self._model.sample_text(
            "Select only the project characters who should enter the opening "
            "simulation scene. Base the decision on location, goals, and the scene "
            "premise. Return the required JSON only; do not provide reasoning.\n"
            f"Locale: {self._content_locale}\nPremise: {self._premise_text}\n"
            f"Candidates:\n{roster}",
            max_tokens=512,
            terminators=(),
            temperature=0.1,
        )
        payload = json.loads(raw)
        selected = payload.get("actor_ids") if isinstance(payload, dict) else None
        if not isinstance(selected, list):
            raise ValueError("Game Master roster output has no actor_ids")
        selected_ids = tuple(
            actor_id
            for actor_id in selected
            if isinstance(actor_id, str) and actor_id in self._candidates
        )
        if not selected_ids:
            raise ValueError("Game Master must select at least one initial actor")
        return tuple(dict.fromkeys(selected_ids))
