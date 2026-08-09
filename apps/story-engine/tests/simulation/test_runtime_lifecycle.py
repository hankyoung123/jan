from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace

import pytest

from story_engine.domain.models import Character, Fact, WorldState
from story_engine.domain.projection import (
    EffectOperation,
    EffectTarget,
    EventVisibility,
    ResolvedEvent,
    ResolvedTurn,
    SimulationBoundary,
    StateEffect,
)
from story_engine.domain.trace import SimulationStage, StageStatus
from story_engine.simulation.runtime import StorySimulationRuntime


def _character(
    character_id: str,
    *,
    type: str = "active",
    resources: tuple[str, ...] = (),
    known_fact_ids: tuple[str, ...] = (),
    beliefs: tuple[str, ...] = (),
    location: str | None = None,
) -> Character:
    return Character(
        id=character_id,
        display_name=character_id,
        type=type,
        identity=f"Identity of {character_id}",
        core_desire=f"Desire of {character_id}",
        current_goal=f"Goal of {character_id}" if type == "active" else None,
        resources=resources,
        known_fact_ids=known_fact_ids,
        beliefs=beliefs,
        location=location,
    )


def _runtime(
    characters: tuple[Character, ...],
    *,
    world: WorldState | None = None,
    canonical_facts: tuple[Fact, ...] = (),
    project_root=None,
    roster_planner=None,
    player_actor_id=None,
) -> StorySimulationRuntime:
    def actor_stub(character_id: str, display_name: str | None = None):
        state: dict[str, object] = {}
        return SimpleNamespace(
            name=character_id,
            display_name=display_name or character_id,
            get_state=lambda: dict(state),
            set_state=lambda value: (state.clear(), state.update(value)),
            set_actor_state=lambda value: state.__setitem__("actor_state", value),
        )

    actor_character = next(
        character for character in characters if character.id == "actor-0"
    )
    actor = actor_stub(actor_character.id, actor_character.display_name)
    available_actors = tuple(
        actor_stub(character.id, character.display_name)
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
        canonical_facts=canonical_facts,
        world=world,
        project_root=project_root,
        game_master_rebuilder=lambda _actors, previous: previous,
        available_actors=available_actors,
        roster_planner=roster_planner,
        initial_roster_selected=True,
        player_actor_id=player_actor_id,
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


def _fact(
    fact_id: str,
    statement: str,
    *,
    visibility: str = "secret",
    known_by: tuple[str, ...] = ("actor-1",),
) -> Fact:
    return Fact(
        id=fact_id,
        statement=statement,
        visibility=visibility,  # type: ignore[arg-type]
        known_by=known_by if visibility != "public" else (),
        source_event_id="seed:test",
        introduced_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


def test_gm_resolution_context_selects_relevant_secret_truth() -> None:
    key_truth = _fact(
        "truth:key-owner",
        "二楼钥匙始终由店主保管。",
    )
    unrelated_truth = _fact(
        "truth:boat-engine",
        "渡船备用发动机昨晚完成了检修。",
    )
    public_background = tuple(
        _fact(
            f"fact:background-{index}",
            f"无关的公开背景记录 {index}。",
            visibility="public",
        )
        for index in range(12)
    )
    runtime = _runtime(
        (
            _character("actor-0", location="旅馆大厅"),
            _character("actor-1", location="旅馆柜台"),
        ),
        canonical_facts=(key_truth, unrelated_truth, *public_background),
        world=WorldState(current_time="18:43", current_location="港口旅馆"),
    )

    context = runtime._resolver_context(
        step=1,
        acting_actor_id="actor-0",
        putative_event_text="我检查是谁拿着二楼钥匙。",
    )

    assert key_truth in context.relevant_canonical_facts
    assert unrelated_truth not in context.relevant_canonical_facts
    assert len(context.relevant_canonical_facts) <= 8
    assert len(context.relevant_canonical_facts) < len(runtime._canonical_facts)
    assert set(context.actor_known_facts).issubset(
        context.relevant_canonical_facts
    )


def test_missing_wiki_does_not_remove_canonical_truth_from_resolution(
    tmp_path,
) -> None:
    key_truth = _fact(
        "truth:key-owner",
        "二楼钥匙始终由店主保管。",
    )
    runtime = _runtime(
        (_character("actor-0"), _character("actor-1")),
        canonical_facts=(key_truth,),
        project_root=tmp_path / "missing-project",
    )

    context = runtime._resolver_context(
        step=1,
        acting_actor_id="actor-0",
        putative_event_text="我寻找二楼钥匙。",
    )

    assert context.relevant_canonical_facts == (key_truth,)
    assert "Wiki unavailable" in context.wiki_context


def test_scene_boundary_keeps_ordinary_npc_out_of_active_agent_roster() -> None:
    class NextRosterPlanner:
        def select_next(
            self,
            candidates,
            *,
            current_roster,
            scene_events,
            min_count,
            max_count,
        ):
            assert (min_count, max_count) == (1, 4)
            assert set(candidates) == {
                "actor-0",
                "actor-1",
                "actor-2",
                "actor-3",
            }
            assert current_roster == ("actor-0",)
            assert scene_events[0].event_id == "event:session-1:0"
            return ("actor-0", "actor-1", "actor-2", "actor-3")

    characters = (
        *(_character(f"actor-{index}") for index in range(4)),
        _character("npc-1", type="npc"),
    )
    runtime = _runtime(
        characters,
        roster_planner=NextRosterPlanner(),
    )

    runtime._advance_scene_boundary(
        _turn(_event("npc-1")),
        event_id="event:session-1:0",
        started_at=datetime.now(UTC),
    )

    npc = next(item for item in runtime.character_states() if item.id == "npc-1")
    assert npc.type == "npc"
    assert sum(item.type == "active" for item in runtime.character_states()) == 4
    assert runtime.roster_actor_ids() == (
        "actor-0",
        "actor-1",
        "actor-2",
        "actor-3",
    )
    assert runtime.pending_scene_events() == ()
    assert [
        (event.stage, event.status) for event in runtime.drain_stage_events()
    ] == [
        (SimulationStage.ACTOR_SELECTION, StageStatus.RUNNING),
        (SimulationStage.ACTOR_SELECTION, StageStatus.SUCCEEDED),
    ]


def test_human_scene_boundary_selects_next_roster_without_promotion() -> None:
    class NextRosterPlanner:
        def select_next(
            self,
            candidates,
            *,
            current_roster,
            scene_events,
            min_count,
            max_count,
        ):
            assert set(candidates) == {"actor-1"}
            assert current_roster == ()
            assert scene_events == (_event("actor-0"),)
            assert (min_count, max_count) == (0, 3)
            return ("actor-1",)

    resolved = _turn(_event("actor-0"))
    runtime = _runtime(
        (_character("actor-0"), _character("actor-1")),
        roster_planner=NextRosterPlanner(),
        player_actor_id="actor-0",
    )
    active_actor_names: list[str] = []
    runtime.actors[0].display_name = "Player A"
    runtime.actors[0].observe = lambda _frame: None
    runtime.game_master = SimpleNamespace(
        set_active_actor=active_actor_names.append
    )
    runtime.resolver = SimpleNamespace(
        resolve=lambda *_args, **_kwargs: resolved,
    )

    result = runtime.execute_human_turn(
        0,
        text="I document the room.",
        cancellation=Event(),
    )

    assert result.boundary == SimulationBoundary.SCENE
    assert active_actor_names == ["Player A"]
    assert runtime.roster_actor_ids() == ("actor-0", "actor-1")
    assert [
        (event.stage, event.status) for event in runtime.drain_stage_events()
    ][-2:] == [
        (SimulationStage.ACTOR_SELECTION, StageStatus.RUNNING),
        (SimulationStage.ACTOR_SELECTION, StageStatus.SUCCEEDED),
    ]


def test_scene_boundary_leaves_dynamic_npc_without_an_actor() -> None:
    runtime = _runtime(
        (_character("actor-0"), _character("npc-1", type="npc")),
    )

    runtime._advance_scene_boundary(
        _turn(_event("npc-1")),
        event_id="event:session-1:0",
        started_at=datetime.now(UTC),
    )

    npc = next(item for item in runtime.character_states() if item.id == "npc-1")
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


def test_runtime_rejects_recreating_an_existing_character_with_clear_guidance() -> None:
    recreate_npc = StateEffect(
        effect_id="effect:recreate:npc-1",
        operation=EffectOperation.CREATE_CHARACTER,
        target=EffectTarget.CHARACTER_PROJECTION,
        target_id="npc-1",
        after={
            "display_name": "NPC One",
            "identity": "A witness",
            "core_desire": "Stay safe",
        },
    )
    runtime = _runtime((_character("actor-0"), _character("npc-1", type="npc")))

    with pytest.raises(
        ValueError,
        match=(
            "GM attempted to recreate existing character 'npc-1'; "
            "reference it through participant_ids instead"
        ),
    ):
        runtime._apply_character_effects(
            _turn(_event("actor-0", "npc-1", effects=(recreate_npc,)))
        )


def test_case_02_resource_updates_are_atomic_and_require_paired_transfer() -> None:
    runtime = _runtime(
        (
            _character("actor-0"),
            _character("actor-1", resources=("二楼备用钥匙",)),
        )
    )
    location_update = StateEffect(
        effect_id="effect:move:actor-0",
        operation=EffectOperation.SET,
        target=EffectTarget.CHARACTER_PROJECTION,
        target_id="actor-0",
        path="location",
        after="旅馆大厅",
    )

    changed = runtime._apply_character_effects(
        _turn(_event("actor-0", effects=(location_update,)))
    )

    moved = next(item for item in runtime.character_states() if item.id == "actor-0")
    assert changed == ("state-updated:actor-0:location",)
    assert moved.location == "旅馆大厅"

    unpaired_key_update = StateEffect(
        effect_id="effect:key:actor-0",
        operation=EffectOperation.SET,
        target=EffectTarget.CHARACTER_PROJECTION,
        target_id="actor-0",
        path="resources",
        after=["二楼备用钥匙"],
    )
    before = runtime.character_states()

    with pytest.raises(ValueError, match="resource transfer must remove"):
        runtime._apply_character_effects(
            _turn(_event("actor-0", effects=(unpaired_key_update,)))
        )

    assert runtime.character_states() == before

    paired_key_removal = StateEffect(
        effect_id="effect:key:actor-1",
        operation=EffectOperation.SET,
        target=EffectTarget.CHARACTER_PROJECTION,
        target_id="actor-1",
        path="resources",
        after=[],
    )
    runtime._apply_character_effects(
        _turn(
            _event(
                "actor-0",
                effects=(paired_key_removal, unpaired_key_update),
            )
        )
    )
    resources = {
        character.id: character.resources for character in runtime.character_states()
    }
    assert resources == {"actor-0": ("二楼备用钥匙",), "actor-1": ()}


def test_case_07_player_belief_changes_do_not_rewrite_world_truth() -> None:
    runtime = _runtime(
        (_character("actor-0"), _character("actor-1")),
        world=WorldState(
            current_time="18:43",
            current_location="旅馆大厅",
            scene_text="张野站在柜台附近。",
        ),
    )
    runtime.player_actor_id = "actor-0"
    belief_update = runtime._human_belief_effect(
        "我认为张野是幕后凶手。",
        step=3,
    )
    assert belief_update is not None
    world_before = runtime.world_state()

    changed = runtime._apply_character_effects(
        _turn(_event("actor-0")).model_copy(
            update={"effects": (belief_update,)}
        )
    )

    player = next(item for item in runtime.character_states() if item.id == "actor-0")
    assert changed == ("state-updated:actor-0:beliefs",)
    assert player.beliefs == ("张野是幕后凶手",)
    assert belief_update.required is True
    assert belief_update.source_record_ids == ("putative:session-1:3",)
    assert runtime.world_state() == world_before


def test_world_time_updates_are_atomic_and_clock_time_cannot_move_backwards() -> None:
    runtime = _runtime(
        (_character("actor-0"),),
        world=WorldState(
            current_time="18:43",
            current_location="旅馆大厅",
        ),
    )
    advance = StateEffect(
        effect_id="effect:time:advance",
        operation=EffectOperation.SET,
        target=EffectTarget.WORLD_PROJECTION,
        path="current_time",
        after="18:49",
    )

    changed = runtime._apply_character_effects(
        _turn(_event("actor-0", effects=(advance,)))
    )

    assert changed == ("world-updated:current_time",)
    assert runtime.world_state() is not None
    assert runtime.world_state().current_time == "18:49"

    backwards = StateEffect(
        effect_id="effect:time:backwards",
        operation=EffectOperation.SET,
        target=EffectTarget.WORLD_PROJECTION,
        path="current_time",
        after="18:42",
    )
    before = runtime.world_state()

    with pytest.raises(ValueError, match="world time cannot move backwards"):
        runtime._apply_character_effects(_turn(_event("actor-0", effects=(backwards,))))

    assert runtime.world_state() == before
