from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from story_engine.domain.models import Character, InitialFact, StoryEvent


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


def test_initial_fact_enforces_knowledge_boundary() -> None:
    with pytest.raises(ValidationError, match="public initial fact"):
        InitialFact(
            id="fact:bell",
            statement="钟声响起。",
            visibility="public",
            known_by=("chen-mo",),
        )


def test_story_event_requires_approval_and_is_immutable() -> None:
    with pytest.raises(ValidationError, match="formal story event requires"):
        StoryEvent(
            id="event-000001",
            sequence=1,
            occurred_at=datetime.now(UTC),
            summary="灯塔熄灭。",
            participants=("chen-mo",),
            source_record_id="session:one",
            approved_by_user=False,
        )

    event = StoryEvent(
        id="event-000001",
        sequence=1,
        occurred_at=datetime.now(UTC),
        summary="灯塔熄灭。",
        participants=("chen-mo",),
        source_record_id="session:one",
        approved_by_user=True,
    )

    with pytest.raises(ValidationError, match="frozen"):
        event.summary = "被原地改写"  # type: ignore[misc]
