import json
from typing import Protocol

from story_engine.domain.models import Character
from story_engine.domain.projection import ResolvedEvent
from story_engine.domain.simulation import PromotionDecision


class PromotionModel(Protocol):
    def sample_text(self, prompt: str, **kwargs: object) -> str: ...


class AutomaticPromotionReviewer:
    """Let the Editor decide NPC promotion at a durable scene boundary."""

    def __init__(self, model: PromotionModel) -> None:
        self.model = model

    def review(
        self,
        character: Character,
        events: tuple[ResolvedEvent, ...],
    ) -> PromotionDecision:
        if character.type != "npc":
            raise ValueError("only an NPC can receive a promotion review")
        evidence_ids = {event.event_id for event in events}
        if not evidence_ids:
            raise ValueError("promotion review requires confirmed scene evidence")
        context = {
            "character": character.model_dump(mode="json"),
            "scene_events": [event.model_dump(mode="json") for event in events],
        }
        prompt = (
            "You are the Story Engine Editor evaluating one ordinary NPC at a "
            "completed scene boundary. Promote only when the NPC has demonstrated "
            "an independent, continuing goal and is likely to initiate consequential "
            "future actions. Ordinary participants must remain NPCs. Return exactly "
            "one PromotionDecision JSON object. character_id must match the supplied "
            "character. When promote is true, provide a concrete proposed_goal and "
            "cite one or more supplied event_id values in evidence_event_ids. When "
            "promote is false, proposed_goal must be null. Context: "
            f"{json.dumps(context, ensure_ascii=False, sort_keys=True)}"
        )
        decision = PromotionDecision.model_validate_json(
            self.model.sample_text(prompt, temperature=0.1)
        )
        if decision.character_id != character.id:
            raise ValueError("promotion decision character does not match candidate")
        unknown_evidence = set(decision.evidence_event_ids) - evidence_ids
        if unknown_evidence:
            raise ValueError(
                f"promotion decision cites unknown evidence: {sorted(unknown_evidence)}"
            )
        return decision
