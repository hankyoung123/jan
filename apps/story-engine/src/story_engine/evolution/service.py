from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Protocol, cast

from pydantic import Field, JsonValue

from story_engine.domain.models import (
    CharacterIntent,
    DomainModel,
    ReviewIssue,
    ReviewResult,
    TurnCandidate,
    WorldOutcome,
    WorldState,
)
from story_engine.events.commit import CommitResult, EventCommitService
from story_engine.events.stream import EventSink
from story_engine.evolution.context import CharacterContext, CharacterContextAssembler
from story_engine.evolution.execution import TurnCancelledError
from story_engine.workspace.candidate_store import CandidateStore
from story_engine.workspace.project_store import ProjectSnapshot, ProjectStore

_MUTABLE_WORLD_FIELDS = frozenset(
    {
        "current_time",
        "current_location",
        "active_pressures",
        "public_fact_ids",
        "world_variables",
    }
)


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
    ) -> WorldOutcome: ...


class EvolutionService:
    def __init__(self, root: Path, *, generator: TurnGenerator) -> None:
        self.root = root
        self.generator = generator
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
        outcome = self.generator.resolve(snapshot.world, intents)
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
                character_id: contexts[character_id].character.version
                for character_id in selected
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
        contexts = self.context_assembler.assemble(current)
        issues: list[ReviewIssue] = []

        if candidate.base_world_version != current.world.version:
            issues.append(
                ReviewIssue(
                    code="world_version_conflict",
                    message="候选基于过期的世界版本。",
                    severity="blocking",
                )
            )
        for intent in candidate.intents:
            context = contexts.get(intent.character_id)
            if context is None:
                issues.append(
                    ReviewIssue(
                        code="unknown_character",
                        message=f"未知或非活跃角色: {intent.character_id}",
                        severity="blocking",
                    )
                )
                continue
            if (
                candidate.base_character_versions.get(intent.character_id)
                != context.character.version
            ):
                issues.append(
                    ReviewIssue(
                        code="character_version_conflict",
                        message=f"{intent.character_id} 的候选版本已过期。",
                        severity="blocking",
                    )
                )
            unavailable = set(intent.knowledge_basis) - set(context.visible_fact_ids)
            if unavailable:
                issues.append(
                    ReviewIssue(
                        code="knowledge_boundary",
                        message=f"{intent.character_id} 使用了无权访问的事实。",
                        severity="blocking",
                        evidence_ids=tuple(sorted(unavailable)),
                    )
                )
            if any(term in intent.action for term in ("成功", "已经", "发现了")):
                issues.append(
                    ReviewIssue(
                        code="intent_decides_outcome",
                        message=f"{intent.character_id} 的行动意图提前决定了结果。",
                        severity="blocking",
                    )
                )

        for change in candidate.outcome.world_changes:
            if change.target_type != "world" or change.target_id != "world":
                issues.append(
                    ReviewIssue(
                        code="world_rule_conflict",
                        message="世界状态变更指向了无效目标。",
                        severity="blocking",
                    )
                )
                continue
            current_value: object
            if change.field.startswith("world_variables."):
                key = change.field.removeprefix("world_variables.")
                if not key:
                    issues.append(
                        ReviewIssue(
                            code="world_rule_conflict",
                            message="世界变量变更缺少变量名称。",
                            severity="blocking",
                        )
                    )
                    continue
                current_value = current.world.world_variables.get(key)
            elif change.field in _MUTABLE_WORLD_FIELDS:
                current_value = current.world.model_dump(mode="json")[change.field]
            else:
                issues.append(
                    ReviewIssue(
                        code="world_rule_conflict",
                        message=f"世界字段不可由回合修改: {change.field}",
                        severity="blocking",
                    )
                )
                continue
            if change.old_value != current_value:
                issues.append(
                    ReviewIssue(
                        code="world_rule_conflict",
                        message=f"世界字段来源值不一致: {change.field}",
                        severity="blocking",
                    )
                )

        passed = not any(issue.severity == "blocking" for issue in issues)
        summary = "知识边界、世界规则和状态来源检查通过。"
        if revision_instruction is not None:
            summary = f"已按修改要求重新检查: {revision_instruction}"
        if not passed:
            summary = "候选未通过编辑检查。"
        review = ReviewResult(
            mode="turn_review",
            passed=passed,
            summary=summary,
            issues=tuple(issues),
        )
        return candidate.with_review(review)

    def request_revision(self, turn_id: str, instruction: str) -> TurnCandidate:
        request = RevisionRequest(instruction=instruction)
        candidate = self.candidate_store.load(turn_id)
        revised_outcome = candidate.outcome.model_copy(
            update={
                "summary": (
                    f"{candidate.outcome.summary} 修改要求: {request.instruction}。"
                )
            }
        )
        draft = candidate.with_outcome(revised_outcome)
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
