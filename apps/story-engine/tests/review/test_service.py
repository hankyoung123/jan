import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from story_engine.domain.models import (
    CharacterIntent,
    StateChange,
    TurnCandidate,
    WorldOutcome,
)
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.gateway import ModelGateway
from story_engine.models.registry import ProfileRegistry
from story_engine.review.service import EditorReviewService, RuleBasedTurnReviewer
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.project_store import ProjectSnapshot, ProjectStore


class ReviewTransport:
    def __init__(self, review: dict[str, object]) -> None:
        self.review = review
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(self.review, ensure_ascii=False),
                    },
                    "finish_reason": "stop",
                }
            ]
        }

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[ModelStreamChunk]:
        del payload, timeout_seconds
        if False:
            yield ModelStreamChunk()
        raise AssertionError("turn review does not stream")


def _snapshot(tmp_path: Path) -> ProjectSnapshot:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    return ProjectStore(tmp_path / "fog-harbor").load()


def _candidate(snapshot: ProjectSnapshot) -> TurnCandidate:
    return TurnCandidate(
        id="turn-000001",
        project_id=snapshot.project.id,
        base_world_version=snapshot.world.version,
        base_character_versions={
            character.id: character.version
            for character in snapshot.characters
            if character.type == "active"
        },
        intents=tuple(
            CharacterIntent(
                character_id=character.id,
                action="检查当前异常" if character.id == "chen-mo" else "呼叫客船减速",
                target=snapshot.world.current_location,
                goal=character.current_goal or character.core_desire,
                knowledge_basis=(character.known_fact_ids[0],),
                recognized_risk="行动可能加剧当前压力",
            )
            for character in snapshot.characters
            if character.type == "active"
        ),
        outcome=WorldOutcome(
            summary="陈默检查灯塔装置, 林岚要求客船减速。",
            public_results=("客船开始减速",),
            world_changes=(
                StateChange(
                    target_type="world",
                    target_id="world",
                    field="world_variables.round",
                    old_value=0,
                    new_value=1,
                    reason="统一结算两个角色的行动",
                ),
            ),
        ),
    )


def _editor(
    tmp_path: Path,
    transport: ReviewTransport,
) -> EditorReviewService:
    return EditorReviewService(
        ModelGateway(ProfileRegistry(tmp_path / "model-registry.json"), transport)
    )


def test_editor_profile_reviews_full_editorial_context(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    transport = ReviewTransport(
        {
            "mode": "turn_review",
            "passed": True,
            "summary": "行动后果符合既有世界规则。",
            "issues": [],
        }
    )

    reviewed = _editor(tmp_path, transport).review(_candidate(snapshot), snapshot)

    assert reviewed.status == "reviewed"
    assert reviewed.review is not None
    assert reviewed.review.summary == "行动后果符合既有世界规则。"
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["model"] == "gpt-5-mini"
    assert "response_format" in call
    prompt = call["messages"][0]["content"]
    assert "灯塔控制港口夜航" in prompt
    assert "secret:chen-father-disappearance" in prompt
    assert "secret:lin-unfiled-duty-roster" in prompt
    assert "陈默检查灯塔装置" in prompt
    assert "turn_review" in prompt


def test_editor_semantic_world_rule_conflict_blocks_candidate(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    transport = ReviewTransport(
        {
            "mode": "turn_review",
            "passed": False,
            "summary": "候选结果违反灯塔的独立供能规则。",
            "issues": [
                {
                    "code": "world_rule_conflict",
                    "message": "港区停电不能直接导致灯塔熄灭。",
                    "severity": "blocking",
                    "evidence_ids": [],
                }
            ],
        }
    )

    reviewed = _editor(tmp_path, transport).review(_candidate(snapshot), snapshot)

    assert reviewed.status == "needs_revision"
    assert reviewed.review is not None
    assert reviewed.review.passed is False
    assert reviewed.review.issues[0].code == "world_rule_conflict"


def test_rule_guard_checks_character_state_source_before_model(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path)
    candidate = _candidate(snapshot)
    conflicted = candidate.with_outcome(
        candidate.outcome.model_copy(
            update={
                "character_changes": (
                    StateChange(
                        target_type="character",
                        target_id="chen-mo",
                        field="location",
                        old_value="不存在的地点",
                        new_value="灯塔一层",
                        reason="陈默进入灯塔",
                    ),
                )
            }
        )
    )
    transport = ReviewTransport(
        {
            "mode": "turn_review",
            "passed": True,
            "summary": "模型试图放行。",
            "issues": [],
        }
    )

    reviewed = _editor(tmp_path, transport).review(conflicted, snapshot)

    assert reviewed.status == "needs_revision"
    assert reviewed.review is not None
    assert any(
        issue.code == "state_source_conflict" for issue in reviewed.review.issues
    )
    assert transport.calls == []


def test_rule_guard_rejects_candidate_from_another_project_before_model(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    candidate = _candidate(snapshot).model_copy(update={"project_id": "other-project"})
    transport = ReviewTransport(
        {
            "mode": "turn_review",
            "passed": True,
            "summary": "模型不能放行其他项目的候选。",
            "issues": [],
        }
    )

    reviewed = _editor(tmp_path, transport).review(candidate, snapshot)

    assert reviewed.status == "needs_revision"
    assert reviewed.review is not None
    assert any(issue.code == "project_mismatch" for issue in reviewed.review.issues)
    assert transport.calls == []


def test_rule_guard_requires_version_for_every_changed_character(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    candidate = _candidate(snapshot)
    chen_intent = next(
        intent for intent in candidate.intents if intent.character_id == "chen-mo"
    )
    lin = next(
        character for character in snapshot.characters if character.id == "lin-lan"
    )
    incomplete = candidate.model_copy(
        update={
            "base_character_versions": {"chen-mo": 0},
            "intents": (chen_intent,),
        }
    ).with_outcome(
        candidate.outcome.model_copy(
            update={
                "character_changes": (
                    StateChange(
                        target_type="character",
                        target_id="lin-lan",
                        field="location",
                        old_value=lin.location,
                        new_value="近港码头",
                        reason="林岚前往码头协调客船",
                    ),
                )
            }
        )
    )
    transport = ReviewTransport(
        {
            "mode": "turn_review",
            "passed": True,
            "summary": "模型不能覆盖版本缺失。",
            "issues": [],
        }
    )

    reviewed = _editor(tmp_path, transport).review(incomplete, snapshot)

    assert reviewed.status == "needs_revision"
    assert reviewed.review is not None
    assert any(
        issue.code == "character_version_conflict" and "lin-lan" in issue.message
        for issue in reviewed.review.issues
    )
    assert transport.calls == []


def test_rule_guard_rejects_wrong_targets_and_duplicate_changes(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(tmp_path)
    candidate = _candidate(snapshot)
    invalid = candidate.with_outcome(
        candidate.outcome.model_copy(
            update={
                "world_changes": (
                    StateChange(
                        target_type="character",
                        target_id="chen-mo",
                        field="location",
                        old_value="灯塔入口",
                        new_value="灯塔一层",
                        reason="错误放入世界变更列表",
                    ),
                    candidate.outcome.world_changes[0],
                    candidate.outcome.world_changes[0],
                )
            }
        )
    )

    reviewed = RuleBasedTurnReviewer().review(invalid, snapshot)

    assert reviewed.status == "needs_revision"
    assert reviewed.review is not None
    assert {issue.code for issue in reviewed.review.issues} >= {
        "state_change_target",
        "duplicate_state_change",
    }
