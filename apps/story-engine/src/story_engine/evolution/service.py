from pathlib import Path
from typing import cast

from pydantic import Field, JsonValue

from story_engine.domain.errors import InvalidTransitionError
from story_engine.domain.models import (
    CharacterIntent,
    DomainModel,
    ReviewIssue,
    ReviewResult,
    StateChange,
    TurnCandidate,
    WorldOutcome,
    WorldState,
)
from story_engine.events.commit import CommitResult, EventCommitService
from story_engine.events.stream import EventSink
from story_engine.evolution.context import CharacterContext, CharacterContextAssembler
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


class EvolutionService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.project_store = ProjectStore(root)
        self.candidate_store = CandidateStore(root)
        self.context_assembler = CharacterContextAssembler()

    def generate_turn(
        self,
        participant_ids: tuple[str, ...] | None = None,
        *,
        event_sink: EventSink | None = None,
    ) -> TurnCandidate:
        snapshot = self.project_store.load()
        contexts = self.context_assembler.assemble(snapshot)
        selected = participant_ids or tuple(sorted(contexts))
        if not selected or any(
            character_id not in contexts for character_id in selected
        ):
            raise ValueError("participants must be active project characters")
        if len(selected) != len(set(selected)):
            raise ValueError("participants must be unique")

        round_number = self._round_number(snapshot.world)
        turn_id = self.candidate_store.next_identifier()
        emit = event_sink or (lambda _event_type, _payload: None)
        emit("turn.started", {"turn_id": turn_id})
        intents_list: list[CharacterIntent] = []
        for character_id in selected:
            emit(
                "character.intent.started",
                {"turn_id": turn_id, "character_id": character_id},
            )
            intent = self._generate_intent(contexts[character_id], round_number)
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
        emit("resolver.started", {"turn_id": turn_id})
        outcome = self._resolve(
            snapshot.world,
            intents,
            round_number,
            {
                character_id: (
                    contexts[character_id].character.display_name or character_id
                )
                for character_id in selected
            },
        )
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
        self.candidate_store.save(reviewed, overwrite=False)
        return reviewed

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

    @staticmethod
    def _round_number(world: WorldState) -> int:
        value = world.world_variables.get("round", 0)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise InvalidTransitionError("world round must be a non-negative integer")
        return value

    @staticmethod
    def _generate_intent(
        context: CharacterContext,
        round_number: int,
    ) -> CharacterIntent:
        fact_id = next(
            (
                item
                for item in context.character.known_fact_ids
                if item in context.visible_fact_ids
            ),
            context.visible_fact_ids[0],
        )
        if context.character.id == "chen-mo":
            actions = ("检查灯塔机械装置", "沿灯塔台阶核对异常痕迹")
        elif context.character.id == "lin-lan":
            actions = ("呼叫客船降低航速", "切换备用航标并报告能见度")
        else:
            actions = ("观察当前局势", "采取符合当前目标的谨慎行动")
        return CharacterIntent(
            character_id=context.character.id,
            action=actions[round_number % len(actions)],
            target=context.world.current_location,
            goal=context.character.current_goal or context.character.core_desire,
            knowledge_basis=(fact_id,),
            recognized_risk="行动可能加剧当前压力",
        )

    @staticmethod
    def _resolve(
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        round_number: int,
        display_names: dict[str, str],
    ) -> WorldOutcome:
        next_round = round_number + 1
        participants = "、".join(
            display_names[intent.character_id] for intent in intents
        )
        return WorldOutcome(
            summary=f"第 {next_round} 轮: {participants} 的行动共同改变了雾港局势。",
            public_results=(f"雾港局势推进至第 {next_round} 轮",),
            world_changes=(
                StateChange(
                    target_type="world",
                    target_id="world",
                    field="world_variables.round",
                    old_value=round_number,
                    new_value=next_round,
                    reason="统一结算所有角色行动后推进回合",
                ),
            ),
            unresolved_consequences=world.active_pressures,
        )
