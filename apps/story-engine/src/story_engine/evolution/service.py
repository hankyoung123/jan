from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Protocol, cast

from pydantic import Field, JsonValue

from story_engine.domain.models import (
    Character,
    CharacterIntent,
    DomainModel,
    TurnCandidate,
    WorldOutcome,
    WorldState,
)
from story_engine.events.commit import CommitResult, EventCommitService
from story_engine.events.stream import EventSink
from story_engine.evolution.context import CharacterContext, CharacterContextAssembler
from story_engine.evolution.execution import TurnCancelledError
from story_engine.review.service import RuleBasedTurnReviewer, TurnReviewer
from story_engine.workspace.candidate_store import CandidateStore
from story_engine.workspace.project_store import ProjectSnapshot, ProjectStore


class RevisionRequest(DomainModel):
    instruction: str = Field(min_length=1, max_length=500)


class TurnGenerationRequest(DomainModel):
    participant_ids: tuple[str, ...] = ()


class TurnGenerator(Protocol):
    def generate_intent(self, context: CharacterContext) -> CharacterIntent: ...

    def resolve(
        self,
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        characters: tuple[Character, ...],
    ) -> WorldOutcome: ...

    def revise(
        self,
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        characters: tuple[Character, ...],
        previous_outcome: WorldOutcome,
        instruction: str,
    ) -> WorldOutcome: ...


class EvolutionService:
    def __init__(
        self,
        root: Path,
        *,
        generator: TurnGenerator,
        reviewer: TurnReviewer | None = None,
    ) -> None:
        self.root = root
        self.generator = generator
        self.reviewer = reviewer or RuleBasedTurnReviewer()
        self.project_store = ProjectStore(root)
        self.candidate_store = CandidateStore(root)
        self.context_assembler = CharacterContextAssembler()

    def generate_turn(
        self,
        participant_ids: tuple[str, ...] | None = None,
        *,
        event_sink: EventSink | None = None,
        cancellation: Event | None = None,
        completion_gate: Callable[[], bool] | None = None,
    ) -> TurnCandidate:
        cancellation = cancellation or Event()
        self._raise_if_cancelled(cancellation)
        snapshot = self.project_store.load()
        contexts = self.context_assembler.assemble(snapshot)
        selected = participant_ids or tuple(sorted(contexts))
        if not selected or any(
            character_id not in contexts for character_id in selected
        ):
            raise ValueError("participants must be active project characters")
        if len(selected) != len(set(selected)):
            raise ValueError("participants must be unique")

        turn_id = self.candidate_store.next_identifier()
        emit = event_sink or (lambda _event_type, _payload: None)
        emit("turn.started", {"turn_id": turn_id})
        self._raise_if_cancelled(cancellation)
        for character_id in selected:
            emit(
                "character.intent.started",
                {"turn_id": turn_id, "character_id": character_id},
            )
        with ThreadPoolExecutor(
            max_workers=len(selected),
            thread_name_prefix="story-character",
        ) as executor:
            intent_futures = {
                character_id: executor.submit(
                    self.generator.generate_intent,
                    contexts[character_id],
                )
                for character_id in selected
            }
            intents_list: list[CharacterIntent] = []
            for character_id in selected:
                intent = intent_futures[character_id].result()
                self._raise_if_cancelled(cancellation)
                if intent.character_id != character_id:
                    raise ValueError(
                        "generator returned an intent for another character"
                    )
                intents_list.append(intent)
                emit(
                    "character.intent.completed",
                    {
                        "turn_id": turn_id,
                        "character_id": character_id,
                        "intent": cast(JsonValue, intent.model_dump(mode="json")),
                    },
                )
        intents = tuple(intents_list)
        self._raise_if_cancelled(cancellation)
        emit("resolver.started", {"turn_id": turn_id})
        outcome = self.generator.resolve(
            snapshot.world,
            intents,
            snapshot.characters,
        )
        self._raise_if_cancelled(cancellation)
        emit(
            "resolver.completed",
            {
                "turn_id": turn_id,
                "outcome": cast(JsonValue, outcome.model_dump(mode="json")),
            },
        )
        candidate = TurnCandidate(
            id=turn_id,
            project_id=snapshot.project.id,
            base_world_version=snapshot.world.version,
            base_character_versions={
                character.id: character.version for character in snapshot.characters
            },
            intents=intents,
            outcome=outcome,
        )
        emit("review.started", {"turn_id": turn_id})
        reviewed = self.review(candidate, snapshot=snapshot)
        self._raise_if_cancelled(cancellation)
        emit(
            "review.completed",
            {
                "turn_id": turn_id,
                "review": cast(
                    JsonValue,
                    reviewed.review.model_dump(mode="json")
                    if reviewed.review
                    else None,
                ),
            },
        )
        if completion_gate is not None and not completion_gate():
            raise TurnCancelledError("turn generation was cancelled")
        self.candidate_store.save(reviewed, overwrite=False)
        return reviewed

    @staticmethod
    def _raise_if_cancelled(cancellation: Event) -> None:
        if cancellation.is_set():
            raise TurnCancelledError("turn generation was cancelled")

    def review(
        self,
        candidate: TurnCandidate,
        *,
        snapshot: ProjectSnapshot | None = None,
        revision_instruction: str | None = None,
    ) -> TurnCandidate:
        current = snapshot or self.project_store.load()
        return self.reviewer.review(
            candidate,
            current,
            revision_instruction=revision_instruction,
        )

    def request_revision(self, turn_id: str, instruction: str) -> TurnCandidate:
        request = RevisionRequest(instruction=instruction)
        candidate = self.candidate_store.load(turn_id)
        snapshot = self.project_store.load()
        draft = candidate.with_outcome(candidate.outcome)
        revised_outcome = self.generator.revise(
            snapshot.world,
            candidate.intents,
            snapshot.characters,
            candidate.outcome,
            request.instruction,
        )
        draft = draft.with_outcome(revised_outcome)
        revised = self.review(draft, revision_instruction=request.instruction)
        self.candidate_store.save(revised)
        return revised

    def confirm(self, turn_id: str) -> CommitResult:
        candidate = self.candidate_store.load(turn_id)
        approved = candidate.approve()
        result = EventCommitService(self.root).commit(approved)
        self.candidate_store.save(result.candidate)
        return result

    def discard(self, turn_id: str) -> TurnCandidate:
        candidate = self.candidate_store.load(turn_id).discard()
        self.candidate_store.save(candidate)
        return candidate
