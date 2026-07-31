from pathlib import Path
from typing import Protocol

from story_engine.domain.errors import InvalidTransitionError
from story_engine.domain.models import Character, StoryEvent
from story_engine.events.commit import EventCommitService, PromotionCommitResult
from story_engine.review.promotion import PromotionAssessment
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectSnapshot, ProjectStore
from story_engine.workspace.promotion_store import PromotionCandidateStore


class PromotionReviewer(Protocol):
    def review(
        self,
        character: Character,
        snapshot: ProjectSnapshot,
        events: tuple[StoryEvent, ...],
    ) -> PromotionAssessment: ...


class CharacterPromotionService:
    def __init__(self, root: Path, *, reviewer: PromotionReviewer) -> None:
        self.root = root
        self.reviewer = reviewer
        self.project_store = ProjectStore(root)
        self.event_store = EventStore(root)
        self.candidate_store = PromotionCandidateStore(root)

    def review(self, character_id: str) -> PromotionAssessment:
        snapshot = self.project_store.load()
        character = self._character(snapshot, character_id)
        assessment = self.reviewer.review(
            character,
            snapshot,
            self.event_store.list_events(),
        )
        if assessment.candidate is None:
            self.candidate_store.delete(character_id)
        else:
            self.candidate_store.save(assessment.candidate)
        return assessment

    def confirm(
        self,
        character_id: str,
        candidate_id: str,
    ) -> PromotionCommitResult:
        candidate = self.candidate_store.load(character_id)
        if candidate.id != candidate_id or candidate.character_id != character_id:
            raise InvalidTransitionError("promotion candidate does not match request")
        result = EventCommitService(self.root).promote_npc(candidate)
        self.candidate_store.save(result.candidate)
        return result

    @staticmethod
    def _character(snapshot: ProjectSnapshot, character_id: str) -> Character:
        for character in snapshot.characters:
            if character.id == character_id:
                return character
        raise FileNotFoundError(character_id)
