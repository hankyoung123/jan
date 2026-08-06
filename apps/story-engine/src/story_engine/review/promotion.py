import json
from typing import Protocol

from story_engine.domain.models import Character
from story_engine.domain.projection import ResolvedEvent
from story_engine.domain.simulation import PromotionDecision, PromotionProposal


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
        if not events:
            raise ValueError("promotion review requires confirmed scene evidence")
        context = {
            "character": {
                "display_name": character.display_name or "Unnamed NPC",
                "identity": character.identity,
                "core_desire": character.core_desire,
                "current_goal": character.current_goal,
                "location": character.location,
                "emotional_state": character.emotional_state,
                "resources": character.resources,
                "relationships": [
                    relationship.description
                    for relationship in character.relationships
                ],
            },
            "scene_events": [
                {
                    "step": event.step,
                    "event_text": event.event_text,
                    "visibility": event.visibility.value,
                    "importance": event.importance,
                    "confidence": event.confidence,
                }
                for event in events
            ],
        }
        prompt = (
            "You are the Story Engine Editor evaluating one ordinary NPC at a "
            "completed scene boundary. Promote only when the NPC has demonstrated "
            "an independent, continuing goal and is likely to initiate consequential "
            "future actions. Ordinary participants must remain NPCs. Return exactly "
            "one promotion proposal JSON object with promote, proposed_goal, and "
            "reason. Do not return character or event IDs; the local runtime binds "
            "this proposal to the supplied candidate and completed scene. When "
            "promote is true, provide a concrete proposed_goal. When promote is "
            "false, proposed_goal must be null. Context: "
            f"{json.dumps(context, ensure_ascii=False, sort_keys=True)}"
        )
        proposal = PromotionProposal.model_validate_json(
            self.model.sample_text(prompt, temperature=0.1)
        )
        return PromotionDecision(
            character_id=character.id,
            evidence_event_ids=tuple(event.event_id for event in events),
            **proposal.model_dump(),
        )
