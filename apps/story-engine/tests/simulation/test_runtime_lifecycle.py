from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from story_engine.domain.models import Character
from story_engine.domain.projection import (
    EffectOperation,
    EffectTarget,
    EventVisibility,
    ResolvedEvent,
    ResolvedTurn,
    SimulationBoundary,
    StateEffect,
)
from story_engine.domain.simulation import PromotionDecision
from story_engine.simulation.runtime import StorySimulationRuntime


def _character(character_id: str, *, type: str = "active") -> Character:
    return Character(
        id=character_id,
        display_name=character_id,
        type=type,
        identity=f"Identity of {character_id}",
        core_desire=f"Desire of {character_id}",
        current_goal=f"Goal of {character_id}" if type == "active" else None,
    )


def _runtime(
    characters: tuple[Character, ...],
    *,
    promotion_reviewer=None,
    promoted_actor_builder=None,
    roster_planner=None,
) -> StorySimulationRuntime:
    actor = SimpleNamespace(name="actor-0")
    available_actors = tuple(
        SimpleNamespace(name=character.id)
        for character in characters
        if character.type == "active" and character.id != actor.name
    )
    return StorySimulationRuntime(
        project_id="project-1",
        session_id="session-1",
        branch_id="main",
        content_locale="en-US",
        actors=(actor,),
        game_master=SimpleNamespace(name="gm"),
        characters=characters,
        promotion_reviewer=promotion_reviewer,
        promoted_actor_builder=promoted_actor_builder,
        game_master_rebuilder=lambda _actors, previous: previous,
        available_actors=available_actors,
        roster_planner=roster_planner,
        initial_roster_selected=True,
    )


def _event(
    *participant_ids: str,
    effects: tuple[StateEffect, ...] = (),
) -> ResolvedEvent:
    return ResolvedEvent(
        event_id="event:session-1:0",
        session_id="session-1",
        step=0,
        event_text="The scene changes.",
        visibility=EventVisibility.PARTICIPANTS,
        participant_ids=participant_ids,
        effects=effects,
        content_locale="en-US",
        occurred_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


def _turn(event: ResolvedEvent) -> ResolvedTurn:
    return ResolvedTurn(
        session_id="session-1",
        branch_id="main",
        step=0,
        raw_resolution_text=event.event_text,
        events=(event,),
        boundary=SimulationBoundary.SCENE,
        content_locale="en-US",
    )


def test_active_agent_pool_can_exceed_four_while_scene_roster_stays_bounded() -> None:
    reviewed: list[str] = []

    class NextRosterPlanner:
        def select_next(self, candidates, *, current_roster, scene_events):
            assert set(candidates) == {
                "actor-0",
                "actor-1",
                "actor-2",
                "actor-3",
                "npc-1",
            }
            assert current_roster == ("actor-0",)
            assert scene_events[0].event_id == "event:session-1:0"
            return ("actor-0", "actor-1", "actor-2", "npc-1")

    def review(
        character: Character,
        events: tuple[ResolvedEvent, ...],
    ) -> PromotionDecision:
        del events
        reviewed.append(character.id)
        return PromotionDecision(
            character_id=character.id,
            promote=True,
            proposed_goal="Take control of the investigation",
            evidence_event_ids=("event:session-1:0",),
            reason="The NPC acted independently.",
        )

    characters = (
        *(_character(f"actor-{index}") for index in range(4)),
        _character("npc-1", type="npc"),
    )
    runtime = _runtime(
        characters,
        promotion_reviewer=review,
        promoted_actor_builder=lambda character, _events: (
            SimpleNamespace(name=character.id),
            object(),
        ),
        roster_planner=NextRosterPlanner(),
    )

    decisions = runtime._evaluate_promotions(_turn(_event("npc-1")))

    npc = next(item for item in runtime.character_states() if item.id == "npc-1")
    assert len(decisions) == 1
    assert decisions[0].promote is True
    assert reviewed == ["npc-1"]
    assert npc.type == "active"
    assert sum(item.type == "active" for item in runtime.character_states()) == 5
    assert runtime.roster_actor_ids() == (
        "actor-0",
        "actor-1",
        "actor-2",
        "npc-1",
    )
    assert runtime.pending_scene_events() == ()


def test_rejected_editor_decision_leaves_npc_without_an_actor() -> None:
    def reject(
        character: Character,
        events: tuple[ResolvedEvent, ...],
    ) -> PromotionDecision:
        del events
        return PromotionDecision(
            character_id=character.id,
            promote=False,
            reason="The NPC only performed a temporary duty.",
        )

    runtime = _runtime(
        (_character("actor-0"), _character("npc-1", type="npc")),
        promotion_reviewer=reject,
        promoted_actor_builder=lambda *_: pytest.fail(
            "a rejected promotion must not build an Actor"
        ),
    )

    decisions = runtime._evaluate_promotions(_turn(_event("npc-1")))

    npc = next(item for item in runtime.character_states() if item.id == "npc-1")
    assert len(decisions) == 1
    assert decisions[0].promote is False
    assert npc.type == "npc"
    assert npc.current_goal is None
    assert runtime.roster_actor_ids() == ("actor-0",)
    assert runtime.pending_scene_events() == ()


def test_unregistered_participant_rejection_is_atomic() -> None:
    create_npc = StateEffect(
        effect_id="effect:create:npc-1",
        operation=EffectOperation.CREATE_CHARACTER,
        target=EffectTarget.CHARACTER_PROJECTION,
        target_id="npc-1",
        after={
            "display_name": "NPC One",
            "identity": "A witness",
            "core_desire": "Stay safe",
        },
    )
    runtime = _runtime((_character("actor-0"),))
    original_characters = runtime.character_states()
    original_roster = runtime.roster_actor_ids()

    with pytest.raises(ValueError, match="unregistered participant IDs"):
        runtime._apply_character_effects(
            _turn(_event("actor-0", "unknown-person", effects=(create_npc,)))
        )

    assert runtime.character_states() == original_characters
    assert runtime.roster_actor_ids() == original_roster
    assert runtime.pending_scene_events() == ()
