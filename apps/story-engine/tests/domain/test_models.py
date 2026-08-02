import pytest
from pydantic import ValidationError

from story_engine.domain.models import Character, InitialFact


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
