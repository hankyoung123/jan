import json
from datetime import UTC, datetime

import pytest

from story_engine.domain.models import Character
from story_engine.domain.projection import EventVisibility, ResolvedEvent
from story_engine.review.promotion import AutomaticPromotionReviewer


class PromotionModel:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.prompts: list[str] = []

    def sample_text(self, prompt: str, **kwargs: object) -> str:
        assert kwargs["temperature"] == 0.1
        self.prompts.append(prompt)
        return json.dumps(self.output, ensure_ascii=False)


def _npc() -> Character:
    return Character(
        id="temporary-pilot",
        display_name="临时引航员",
        type="npc",
        identity="暴风雨中赶到港口的引航员",
        core_desire="让客船安全避开暗礁",
        location="近港码头",
    )


def _event() -> ResolvedEvent:
    return ResolvedEvent(
        event_id="event:session:1:4",
        session_id="session:1",
        step=4,
        event_text="引航员违抗命令, 独自驾艇去警告客船。",
        visibility=EventVisibility.PARTICIPANTS,
        participant_ids=("temporary-pilot",),
        content_locale="zh-CN",
        occurred_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


def test_editor_automatically_recommends_promotion_from_scene_evidence() -> None:
    model = PromotionModel(
        {
            "promote": True,
            "proposed_goal": "主动引导客船避开近港暗礁",
            "reason": "已经表现出独立、持续的行动目标。",
        }
    )

    decision = AutomaticPromotionReviewer(model).review(_npc(), (_event(),))

    assert decision.promote is True
    assert decision.proposed_goal == "主动引导客船避开近港暗礁"
    assert decision.evidence_event_ids == ("event:session:1:4",)
    assert "completed scene boundary" in model.prompts[0]
    assert "Do not return character or event IDs" in model.prompts[0]
    assert "temporary-pilot" not in model.prompts[0]
    assert "event:session:1:4" not in model.prompts[0]
    assert "临时引航员" in model.prompts[0]


def test_editor_may_leave_an_ordinary_person_as_npc() -> None:
    model = PromotionModel(
        {
            "promote": False,
            "proposed_goal": None,
            "reason": "该人物只是在履行临时职责。",
        }
    )

    decision = AutomaticPromotionReviewer(model).review(_npc(), (_event(),))

    assert decision.promote is False


def test_editor_rejects_model_authored_evidence_ids() -> None:
    model = PromotionModel(
        {
            "promote": True,
            "proposed_goal": "追查幕后指使者",
            "evidence_event_ids": ["event:invented:99"],
            "reason": "引用了不存在的证据。",
        }
    )

    with pytest.raises(ValueError, match="evidence_event_ids"):
        AutomaticPromotionReviewer(model).review(_npc(), (_event(),))


def test_only_npcs_can_receive_automatic_promotion_review() -> None:
    active = _npc().model_copy(
        update={"type": "active", "current_goal": "引导客船"}
    )
    model = PromotionModel({})

    with pytest.raises(ValueError, match="only an NPC"):
        AutomaticPromotionReviewer(model).review(active, (_event(),))
