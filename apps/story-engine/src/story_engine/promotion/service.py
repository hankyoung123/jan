from pathlib import Path
from typing import Protocol

from story_engine.domain.errors import InvalidTransitionError
from story_engine.domain.models import Character, DomainModel, PromotionCandidate
from story_engine.domain.projection import ResolvedEvent
from story_engine.persistence.simulation_log import SimulationLogStore
from story_engine.review.promotion import PromotionAssessment
from story_engine.workspace.documents import render_character
from story_engine.workspace.lock import ProjectLock
from story_engine.workspace.project_store import ProjectSnapshot, ProjectStore
from story_engine.workspace.promotion_store import PromotionProposalStore
from story_engine.workspace.session import canonical_revision
from story_engine.workspace.transaction import AtomicBatch


class PromotionCommitResult(DomainModel):
    character: Character
    candidate: PromotionCandidate


class PromotionReviewer(Protocol):
    def review(
        self,
        character: Character,
        snapshot: ProjectSnapshot,
        events: tuple[ResolvedEvent, ...],
    ) -> PromotionAssessment: ...


class CharacterPromotionService:
    def __init__(self, root: Path, *, reviewer: PromotionReviewer) -> None:
        self.root = root
        self.reviewer = reviewer
        self.project_store = ProjectStore(root)
        self.proposal_store = PromotionProposalStore(root)
        self.logs = SimulationLogStore(root)

    def review(self, character_id: str, branch_id: str) -> PromotionAssessment:
        snapshot = self.project_store.load()
        base_workspace_revision = canonical_revision(self.root)
        character = self._character(snapshot, character_id)
        events = tuple(
            event
            for record in self.logs.read(branch_id)
            if record.result.resolved_turn is not None
            for event in record.result.resolved_turn.events
        )
        assessment = self.reviewer.review(character, snapshot, events)
        if assessment.candidate is None:
            self.proposal_store.delete(character_id)
        else:
            candidate = assessment.candidate.model_copy(
                update={"base_workspace_revision": base_workspace_revision}
            )
            self.proposal_store.save(candidate)
            assessment = assessment.model_copy(update={"candidate": candidate})
        return assessment

    def confirm(
        self,
        character_id: str,
        candidate_id: str,
    ) -> PromotionCommitResult:
        candidate = self.proposal_store.load(character_id)
        if candidate.id != candidate_id or candidate.character_id != character_id:
            raise InvalidTransitionError("promotion candidate does not match request")
        if candidate.status != "pending":
            raise InvalidTransitionError("only a pending promotion can be committed")
        with ProjectLock(self.root):
            if canonical_revision(self.root) != candidate.base_workspace_revision:
                raise InvalidTransitionError(
                    "canonical workspace changed since promotion review"
                )
            snapshot = self.project_store.load()
            current = self._character(snapshot, character_id)
            if current.type != "npc":
                raise InvalidTransitionError("only an NPC can be promoted")
            if current.version != candidate.base_character_version:
                raise InvalidTransitionError(
                    "character version changed since promotion review"
                )
            promoted = Character.model_validate(
                current.model_copy(
                    update={
                        "type": "active",
                        "current_goal": candidate.proposed_goal,
                        "version": current.version + 1,
                    }
                )
            )
            previous_path = self.project_store.character_path(current).relative_to(
                self.root
            )
            promoted_path = self.project_store.character_path(promoted).relative_to(
                self.root
            )
            batch = AtomicBatch(self.root)
            batch.add(
                promoted_path.as_posix(),
                render_character(promoted, snapshot.facts),
                overwrite=False,
            )
            batch.delete(previous_path.as_posix())
            batch.commit()
        committed = candidate.mark_committed()
        self.proposal_store.save(committed)
        return PromotionCommitResult(character=promoted, candidate=committed)

    @staticmethod
    def _character(snapshot: ProjectSnapshot, character_id: str) -> Character:
        for character in snapshot.characters:
            if character.id == character_id:
                return character
        raise FileNotFoundError(character_id)
