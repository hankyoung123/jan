from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from story_engine.domain.errors import InvalidTransitionError
from story_engine.domain.models import (
    Character,
    CharacterIntent,
    NpcCandidate,
    ReviewResult,
    StoryEvent,
    TurnCandidate,
    WorldOutcome,
)


def _intent() -> CharacterIntent:
    return CharacterIntent(
        character_id="chen-mo",
        action="检查灯芯槽",
        target="灯塔照明装置",
        goal="判断灯塔是否被人为关闭",
        knowledge_basis=("knowledge:lighthouse-never-off",),
        recognized_risk="可能暴露自己的调查",
    )


def _outcome(summary: str = "陈默在灯芯槽中发现了新鲜刮痕。") -> WorldOutcome:
    return WorldOutcome(summary=summary)


def _candidate() -> TurnCandidate:
    return TurnCandidate(
        id="turn-000001",
        project_id="fog-harbor",
        base_world_version=1,
        base_character_versions={"chen-mo": 2},
        intents=(_intent(),),
        outcome=_outcome(),
    )


def test_active_character_requires_current_goal() -> None:
    with pytest.raises(ValidationError, match="active character requires"):
        Character(
            id="chen-mo",
            type="active",
            identity="机械工程师",
            core_desire="查明父亲失踪的真相",
            current_goal=None,
        )


def test_npc_may_exist_without_current_goal() -> None:
    character = Character(
        id="keeper",
        type="npc",
        identity="守塔人",
        core_desire="保住工作",
    )

    assert character.current_goal is None


def test_world_outcome_rejects_duplicate_npc_ids() -> None:
    with pytest.raises(ValidationError, match="NPC IDs must be unique"):
        WorldOutcome(
            summary="港口需要一名临时引航员。",
            new_npcs=(
                NpcCandidate(
                    id="temporary-pilot",
                    identity="临时引航员",
                    purpose="引导客船避开暗礁",
                ),
                NpcCandidate(
                    id="temporary-pilot",
                    identity="另一名引航员",
                    purpose="维持港口秩序",
                ),
            ),
        )


def test_character_intent_rejects_result_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CharacterIntent.model_validate(
            {
                "character_id": "chen-mo",
                "action": "检查灯芯槽",
                "target": "灯塔照明装置",
                "goal": "判断灯塔是否被人为关闭",
                "knowledge_basis": ["knowledge:lighthouse-never-off"],
                "recognized_risk": None,
                "result": "成功找到了证据",
            }
        )


def test_story_event_requires_approval_and_is_immutable() -> None:
    with pytest.raises(ValidationError, match="formal story event requires"):
        StoryEvent(
            id="event-000001",
            sequence=1,
            occurred_at=datetime.now(UTC),
            summary="灯塔熄灭。",
            participants=("chen-mo",),
            source_turn_id="turn-000001",
            approved_by_user=False,
        )

    event = StoryEvent(
        id="event-000001",
        sequence=1,
        occurred_at=datetime.now(UTC),
        summary="灯塔熄灭。",
        participants=("chen-mo",),
        source_turn_id="turn-000001",
        approved_by_user=True,
    )

    with pytest.raises(ValidationError, match="frozen"):
        event.summary = "被原地改写"  # type: ignore[misc]


def test_candidate_cannot_approve_without_passing_review() -> None:
    candidate = _candidate()

    with pytest.raises(InvalidTransitionError, match="passing current review"):
        candidate.approve()

    rejected = candidate.with_review(
        ReviewResult(
            mode="turn_review",
            passed=False,
            summary="知识依据不足。",
        )
    )
    with pytest.raises(InvalidTransitionError, match="passing current review"):
        rejected.approve()


def test_candidate_review_approval_and_edit_invalidation() -> None:
    reviewed = _candidate().with_review(
        ReviewResult(
            mode="turn_review",
            passed=True,
            summary="行动与世界规则一致。",
        )
    )

    assert reviewed.status == "reviewed"
    approved = reviewed.approve()
    assert approved.status == "approved"

    edited = approved.with_outcome(_outcome("刮痕来自近期人为拆卸。"))
    assert edited.status == "draft"
    assert edited.review is None


def test_candidate_json_contract_uses_snake_case_and_arrays() -> None:
    payload = _candidate().model_dump(mode="json")

    assert payload["base_world_version"] == 1
    assert payload["base_character_versions"] == {"chen-mo": 2}
    assert payload["intents"][0]["knowledge_basis"] == [
        "knowledge:lighthouse-never-off"
    ]
