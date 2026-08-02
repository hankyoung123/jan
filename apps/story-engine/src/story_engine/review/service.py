import asyncio
import json
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import Protocol

from story_engine.domain.models import (
    Character,
    ReviewIssue,
    ReviewResult,
    StateChange,
    TurnCandidate,
)
from story_engine.evolution.context import CharacterContextAssembler
from story_engine.evolution.execution import TurnCancelledError
from story_engine.models.contracts import Message, ModelRequest, ModelResponse
from story_engine.models.gateway import ModelGateway
from story_engine.rag.models import (
    RagSearchRequest,
    RetrievalEvidence,
    RetrievalScope,
)
from story_engine.rag.service import RagService
from story_engine.workspace.project_store import ProjectSnapshot

_MUTABLE_CHARACTER_FIELDS = frozenset(
    {
        "current_goal",
        "location",
        "emotional_state",
        "resources",
    }
)
_MUTABLE_WORLD_FIELDS = frozenset(
    {
        "current_time",
        "current_location",
        "active_pressures",
        "world_variables",
    }
)
_MAX_EDITOR_EVIDENCE = 24


@dataclass(frozen=True, slots=True)
class EditorialClaim:
    id: str
    text: str
    evidence_ids: tuple[str, ...] = ()


class TurnReviewer(Protocol):
    def review(
        self,
        candidate: TurnCandidate,
        snapshot: ProjectSnapshot,
        *,
        revision_instruction: str | None = None,
    ) -> TurnCandidate: ...


class RuleBasedTurnReviewer:
    """Enforce review invariants that a model is never allowed to override."""

    def __init__(self) -> None:
        self.context_assembler = CharacterContextAssembler()

    def evaluate(
        self,
        candidate: TurnCandidate,
        snapshot: ProjectSnapshot,
    ) -> tuple[ReviewIssue, ...]:
        issues: list[ReviewIssue] = []
        contexts = self.context_assembler.assemble(snapshot)
        characters = {character.id: character for character in snapshot.characters}

        if candidate.project_id != snapshot.project.id:
            issues.append(
                ReviewIssue(
                    code="project_mismatch",
                    message="候选所属项目与当前工作区不一致。",
                    severity="blocking",
                    evidence_ids=(snapshot.project.id,),
                )
            )

        if candidate.base_world_version != snapshot.world.version:
            issues.append(
                ReviewIssue(
                    code="world_version_conflict",
                    message="候选基于过期的世界版本。",
                    severity="blocking",
                    evidence_ids=(f"world:v{snapshot.world.version}",),
                )
            )

        required_character_versions = {
            intent.character_id for intent in candidate.intents
        }
        required_character_versions.update(
            change.target_id
            for change in candidate.outcome.character_changes
            if change.target_type == "character"
        )
        for character_id in sorted(required_character_versions):
            current = characters.get(character_id)
            if current is None:
                continue
            if candidate.base_character_versions.get(character_id) != current.version:
                issues.append(
                    ReviewIssue(
                        code="character_version_conflict",
                        message=f"{character_id} 的候选版本缺失或已过期。",
                        severity="blocking",
                        evidence_ids=(f"character:{character_id}:v{current.version}",),
                    )
                )

        for character_id, base_version in sorted(
            candidate.base_character_versions.items()
        ):
            current = characters.get(character_id)
            if current is None or current.version != base_version:
                if character_id in required_character_versions:
                    continue
                issues.append(
                    ReviewIssue(
                        code="character_version_conflict",
                        message=f"{character_id} 的候选版本已过期。",
                        severity="blocking",
                        evidence_ids=(f"character:{character_id}",),
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
            visible_fact_ids = {
                fact.id for fact in context.perception.visible_facts
            }
            unavailable = set(intent.knowledge_basis) - visible_fact_ids
            if unavailable:
                issues.append(
                    ReviewIssue(
                        code="knowledge_boundary",
                        message=f"{intent.character_id} 使用了无权访问的事实。",
                        severity="blocking",
                        evidence_ids=tuple(sorted(unavailable)),
                    )
                )
        seen_changes: set[tuple[str, str, str]] = set()
        for expected_type, changes in (
            ("character", candidate.outcome.character_changes),
            ("world", candidate.outcome.world_changes),
        ):
            for change in changes:
                key = (change.target_type, change.target_id, change.field)
                if key in seen_changes:
                    issues.append(
                        ReviewIssue(
                            code="duplicate_state_change",
                            message=(
                                "同一候选重复修改状态字段: "
                                f"{change.target_id}.{change.field}"
                            ),
                            severity="blocking",
                        )
                    )
                    continue
                seen_changes.add(key)
                if change.target_type != expected_type:
                    issues.append(
                        ReviewIssue(
                            code="state_change_target",
                            message=f"状态变更位于错误的 {expected_type} 变更列表。",
                            severity="blocking",
                        )
                    )
                    continue
                if expected_type == "world":
                    self._review_world_change(change, snapshot, issues)
                else:
                    self._review_character_change(change, characters, issues)

        existing_character_ids = set(characters)
        created_character_ids = {npc.id for npc in candidate.outcome.new_npcs}
        for npc in candidate.outcome.new_npcs:
            if npc.id in existing_character_ids:
                issues.append(
                    ReviewIssue(
                        code="npc_id_conflict",
                        message=f"普通人物 ID 已被现有角色使用: {npc.id}",
                        severity="blocking",
                    )
                )
        existing_fact_ids = {fact.id for fact in snapshot.facts}
        candidate_fact_ids = {
            fact.id for fact in candidate.outcome.fact_candidates
        }
        available_character_ids = existing_character_ids | created_character_ids
        available_fact_ids = existing_fact_ids | candidate_fact_ids
        for fact in candidate.outcome.fact_candidates:
            if fact.id in existing_fact_ids:
                issues.append(
                    ReviewIssue(
                        code="fact_id_conflict",
                        message=f"事实 ID 已存在: {fact.id}",
                        severity="blocking",
                        evidence_ids=(fact.id,),
                    )
                )
            unknown = set(fact.known_by) - available_character_ids
            if unknown:
                issues.append(
                    ReviewIssue(
                        code="fact_knowledge_target",
                        message=f"事实引用了未知知情角色: {fact.id}",
                        severity="blocking",
                        evidence_ids=tuple(sorted(unknown)),
                    )
                )
            if (
                fact.supersedes_fact_id is not None
                and fact.supersedes_fact_id not in available_fact_ids
            ):
                issues.append(
                    ReviewIssue(
                        code="fact_supersedes_unknown",
                        message=f"事实替代了未知事实: {fact.supersedes_fact_id}",
                        severity="blocking",
                        evidence_ids=(fact.supersedes_fact_id,),
                    )
                )
        for knowledge_change in candidate.outcome.knowledge_changes:
            if knowledge_change.character_id not in available_character_ids:
                issues.append(
                    ReviewIssue(
                        code="knowledge_change_target",
                        message=(
                            "知识变更引用了未知角色: "
                            f"{knowledge_change.character_id}"
                        ),
                        severity="blocking",
                    )
                )
            if knowledge_change.fact_id not in available_fact_ids:
                issues.append(
                    ReviewIssue(
                        code="knowledge_change_fact",
                        message=f"知识变更引用了未知事实: {knowledge_change.fact_id}",
                        severity="blocking",
                        evidence_ids=(knowledge_change.fact_id,),
                    )
                )
        return tuple(issues)

    @staticmethod
    def _review_world_change(
        change: StateChange,
        snapshot: ProjectSnapshot,
        issues: list[ReviewIssue],
    ) -> None:
        if change.target_id != "world":
            issues.append(
                ReviewIssue(
                    code="state_change_target",
                    message=f"未知世界状态目标: {change.target_id}",
                    severity="blocking",
                )
            )
            return
        if change.field.startswith("world_variables."):
            key = change.field.removeprefix("world_variables.")
            if not key:
                issues.append(
                    ReviewIssue(
                        code="state_change_forbidden",
                        message="世界变量变更缺少变量名称。",
                        severity="blocking",
                    )
                )
                return
            current_value = snapshot.world.world_variables.get(key)
        elif change.field in _MUTABLE_WORLD_FIELDS:
            current_value = snapshot.world.model_dump(mode="json")[change.field]
        else:
            issues.append(
                ReviewIssue(
                    code="state_change_forbidden",
                    message=f"世界字段不可由回合修改: {change.field}",
                    severity="blocking",
                )
            )
            return
        if change.old_value != current_value:
            issues.append(
                ReviewIssue(
                    code="state_source_conflict",
                    message=f"世界字段来源值不一致: {change.field}",
                    severity="blocking",
                    evidence_ids=(f"world:v{snapshot.world.version}",),
                )
            )

    @staticmethod
    def _review_character_change(
        change: StateChange,
        characters: Mapping[str, Character],
        issues: list[ReviewIssue],
    ) -> None:
        character = characters.get(change.target_id)
        if character is None:
            issues.append(
                ReviewIssue(
                    code="state_change_target",
                    message=f"未知角色状态目标: {change.target_id}",
                    severity="blocking",
                )
            )
            return
        if change.field not in _MUTABLE_CHARACTER_FIELDS:
            issues.append(
                ReviewIssue(
                    code="state_change_forbidden",
                    message=f"角色字段不可由回合修改: {change.field}",
                    severity="blocking",
                )
            )
            return
        current = character.model_dump(mode="json")
        if change.old_value != current[change.field]:
            issues.append(
                ReviewIssue(
                    code="state_source_conflict",
                    message=(
                        f"角色字段来源值不一致: {change.target_id}.{change.field}"
                    ),
                    severity="blocking",
                    evidence_ids=(
                        f"character:{change.target_id}:v{current['version']}",
                    ),
                )
            )

    def review(
        self,
        candidate: TurnCandidate,
        snapshot: ProjectSnapshot,
        *,
        revision_instruction: str | None = None,
    ) -> TurnCandidate:
        issues = self.evaluate(candidate, snapshot)
        passed = not any(issue.severity == "blocking" for issue in issues)
        summary = "知识边界、状态来源和结构化规则检查通过。"
        if revision_instruction is not None:
            summary = f"已按修改要求重新检查: {revision_instruction}"
        if not passed:
            summary = "候选未通过确定性编辑检查。"
        return candidate.with_review(
            ReviewResult(
                mode="turn_review",
                passed=passed,
                summary=summary,
                issues=issues,
            )
        )


class EditorReviewService:
    """Use the Editor profile for semantic review after deterministic guards."""

    def __init__(
        self,
        model_gateway: ModelGateway,
        *,
        root: Path,
        cancellation: Event | None = None,
        policy: RuleBasedTurnReviewer | None = None,
    ) -> None:
        self.model_gateway = model_gateway
        self.rag = RagService(root)
        self.cancellation = cancellation
        self.policy = policy or RuleBasedTurnReviewer()

    @staticmethod
    def _claims(candidate: TurnCandidate) -> tuple[EditorialClaim, ...]:
        claims = [
            EditorialClaim(
                id="outcome:summary",
                text=candidate.outcome.summary,
            )
        ]
        claims.extend(
            EditorialClaim(
                id=f"intent:{intent.character_id}",
                text=" ".join(
                    value
                    for value in (
                        intent.action,
                        intent.target,
                        intent.goal,
                        intent.recognized_risk,
                    )
                    if value
                ),
                evidence_ids=intent.knowledge_basis,
            )
            for intent in candidate.intents
        )
        claims.extend(
            EditorialClaim(
                id=f"fact:{fact.id}",
                text=fact.statement,
                evidence_ids=(
                    (fact.supersedes_fact_id,)
                    if fact.supersedes_fact_id is not None
                    else ()
                ),
            )
            for fact in candidate.outcome.fact_candidates
        )
        claims.extend(
            EditorialClaim(
                id=f"state:{change.target_type}:{change.target_id}:{change.field}",
                text=(
                    f"{change.reason} {change.target_id} {change.field} "
                    f"{change.old_value!r} -> {change.new_value!r}"
                ),
            )
            for change in (
                *candidate.outcome.character_changes,
                *candidate.outcome.world_changes,
            )
        )
        claims.extend(
            EditorialClaim(
                id=(
                    f"knowledge:{change.character_id}:{change.fact_id}:"
                    f"{change.action}"
                ),
                text=change.reason,
                evidence_ids=(change.fact_id,),
            )
            for change in candidate.outcome.knowledge_changes
        )
        return tuple(claims)

    def _evidence_for(
        self,
        claims: tuple[EditorialClaim, ...],
    ) -> tuple[RetrievalEvidence, ...]:
        scope = RetrievalScope(kind="editorial")
        evidence: list[RetrievalEvidence] = []
        seen_chunks: set[str] = set()

        def add_hits(request: RagSearchRequest) -> None:
            for hit in self.rag.search(request).hits:
                if hit.chunk_id in seen_chunks:
                    continue
                seen_chunks.add(hit.chunk_id)
                evidence.append(
                    RetrievalEvidence(
                        task="editor",
                        **hit.model_dump(mode="python"),
                    )
                )
                if len(evidence) >= _MAX_EDITOR_EVIDENCE:
                    return

        add_hits(
            RagSearchRequest(
                exact_id="world",
                scope=scope,
                limit=12,
            )
        )
        for evidence_id in dict.fromkeys(
            evidence_id
            for claim in claims
            for evidence_id in claim.evidence_ids
        ):
            add_hits(
                RagSearchRequest(
                    exact_id=evidence_id,
                    scope=scope,
                    limit=4,
                )
            )
            if len(evidence) >= _MAX_EDITOR_EVIDENCE:
                return tuple(evidence)

        for claim in claims:
            add_hits(
                RagSearchRequest(
                    query=claim.text,
                    scope=scope,
                    limit=3,
                )
            )
            if len(evidence) >= _MAX_EDITOR_EVIDENCE:
                break
        return tuple(evidence)

    async def _complete_with_cancellation(self, request: ModelRequest) -> ModelResponse:
        if self.cancellation is None:
            return await self.model_gateway.complete(request)
        if self.cancellation.is_set():
            raise TurnCancelledError("turn generation was cancelled")
        completion = asyncio.create_task(self.model_gateway.complete(request))
        while not completion.done():
            if self.cancellation.is_set():
                completion.cancel()
                with suppress(asyncio.CancelledError):
                    await completion
                raise TurnCancelledError("turn generation was cancelled")
            await asyncio.wait({completion}, timeout=0.05)
        return completion.result()

    def review(
        self,
        candidate: TurnCandidate,
        snapshot: ProjectSnapshot,
        *,
        revision_instruction: str | None = None,
    ) -> TurnCandidate:
        deterministic_issues = self.policy.evaluate(candidate, snapshot)
        if any(issue.severity == "blocking" for issue in deterministic_issues):
            return candidate.with_review(
                ReviewResult(
                    mode="turn_review",
                    passed=False,
                    summary="候选未通过确定性编辑检查。",
                    issues=deterministic_issues,
                )
            )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            raise RuntimeError("Editor review must run outside the API event loop")

        claims = self._claims(candidate)
        evidence = self._evidence_for(claims)
        context = {
            "candidate": candidate.model_dump(mode="json"),
            "claims": [
                {
                    "id": claim.id,
                    "text": claim.text,
                    "evidence_ids": claim.evidence_ids,
                }
                for claim in claims
            ],
            "retrieved_evidence": [
                item.model_dump(mode="json") for item in evidence
            ],
            "revision_instruction": revision_instruction,
        }
        prompt = (
            "You are the single Story Engine Editor operating in turn_review mode. "
            "Review each structured claim against the retrieved editorial evidence. "
            "An intent may describe only an attempted action: block it when its "
            "meaning asserts success, a completed discovery, another character's "
            "response, or a new world fact, regardless of language or wording. Check "
            "that the resolved outcome follows the evidence and completed intents, "
            "and that every state-change reason has a credible source. Do not infer "
            "missing evidence from general knowledge. Cite supplied source_id or "
            "chunk_id values in each issue's evidence_ids. You may inspect all "
            "retrieved editorial evidence, including private facts, but must not "
            "approve on the user's behalf. Return exactly one ReviewResult JSON "
            "object with mode turn_review. Context: "
            f"{json.dumps(context, ensure_ascii=False, sort_keys=True)}"
        )
        response = asyncio.run(
            self._complete_with_cancellation(
                ModelRequest(
                    profile_id="editor",
                    task_type="editor",
                    messages=(Message(role="system", content=prompt),),
                    output_schema=json.dumps(
                        ReviewResult.model_json_schema(),
                        ensure_ascii=False,
                    ),
                    max_output_tokens=2048,
                    timeout_seconds=60,
                    temperature=0.1,
                )
            )
        )
        model_review = ReviewResult.model_validate(response.parsed_output)
        if model_review.mode != "turn_review":
            raise ValueError("Editor turn review requires turn_review mode")
        issues = (*deterministic_issues, *model_review.issues)
        passed = model_review.passed and not any(
            issue.severity == "blocking" for issue in issues
        )
        return candidate.with_review(
            ReviewResult(
                mode="turn_review",
                passed=passed,
                summary=model_review.summary,
                issues=issues,
            )
        )
