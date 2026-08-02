import asyncio
import json
from typing import Self

from pydantic import model_validator

from story_engine.domain.errors import InvalidTransitionError
from story_engine.domain.models import (
    Character,
    DomainModel,
    PromotionCandidate,
    ReviewResult,
)
from story_engine.domain.projection import ResolvedEvent
from story_engine.models.contracts import Message, ModelRequest
from story_engine.models.gateway import ModelGateway
from story_engine.workspace.project_store import ProjectSnapshot


class PromotionReviewOutput(DomainModel):
    review: ReviewResult
    proposed_goal: str | None = None

    @model_validator(mode="after")
    def recommendation_has_goal(self) -> Self:
        if self.review.mode != "promotion_review":
            raise ValueError("promotion review requires promotion_review mode")
        if self.review.passed and not self.proposed_goal:
            raise ValueError("recommended promotion requires a proposed goal")
        if not self.review.passed and self.proposed_goal is not None:
            raise ValueError("rejected promotion cannot propose an active goal")
        return self


class PromotionAssessment(DomainModel):
    project_id: str
    character_id: str
    review: ReviewResult
    candidate: PromotionCandidate | None = None


class EditorPromotionReviewer:
    """Ask the single Editor profile whether an NPC merits active agency."""

    def __init__(self, model_gateway: ModelGateway) -> None:
        self.model_gateway = model_gateway

    def review(
        self,
        character: Character,
        snapshot: ProjectSnapshot,
        events: tuple[ResolvedEvent, ...],
    ) -> PromotionAssessment:
        if character.type != "npc":
            raise InvalidTransitionError("only an NPC can receive a promotion review")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("promotion review must run outside the API event loop")

        context = {
            "project": snapshot.project.model_dump(mode="json"),
            "world": snapshot.world.model_dump(mode="json"),
            "character": character.model_dump(mode="json"),
            "related_events": [
                event.model_dump(mode="json")
                for event in events
                if character.id in event.participant_ids
                or character.id in event.event_text
            ],
        }
        prompt = (
            "You are the single Story Engine Editor operating in promotion_review "
            "mode. Recommend promotion only when this NPC has formed an independent "
            "goal and may proactively affect future story events. A recommendation "
            "is not user approval and must not modify canonical state. If recommended, "
            "return a concrete proposed_goal suitable for an active Character. Return "
            "exactly one PromotionReviewOutput JSON object. Context: "
            f"{json.dumps(context, ensure_ascii=False, sort_keys=True)}"
        )
        response = asyncio.run(
            self.model_gateway.complete(
                ModelRequest(
                    profile_id="editor",
                    task_type="editor",
                    messages=(Message(role="system", content=prompt),),
                    output_schema=json.dumps(
                        PromotionReviewOutput.model_json_schema(),
                        ensure_ascii=False,
                    ),
                    max_output_tokens=1536,
                    timeout_seconds=60,
                    temperature=0.1,
                )
            )
        )
        output = PromotionReviewOutput.model_validate(response.parsed_output)
        candidate = None
        if output.review.passed:
            assert output.proposed_goal is not None
            candidate = PromotionCandidate(
                id=f"promotion-{character.id}-v{character.version}",
                project_id=snapshot.project.id,
                character_id=character.id,
                base_character_version=character.version,
                proposed_goal=output.proposed_goal,
                review=output.review,
            )
        return PromotionAssessment(
            project_id=snapshot.project.id,
            character_id=character.id,
            review=output.review,
            candidate=candidate,
        )
