from threading import Event

from story_engine.concordia_runtime.factory import (
    ConcordiaActorFactory,
    default_character_recipe,
    default_game_master_recipe,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.replay import ReplayLanguageModel
from story_engine.concordia_runtime.resolver import (
    ConcordiaResolverKernel,
    ResolutionEnvelopeError,
    SimulationCancelledError,
)
from story_engine.domain.memory import MemoryRecordType, MemoryScope
from story_engine.domain.simulation import ResolverContext


def _runtime(
    resolution_text: str | None = None,
) -> tuple[object, object, ConcordiaMemoryBank]:
    actor_model = ReplayLanguageModel(text_responses=("I force the locked door.",))
    gm_model = ReplayLanguageModel(
        text_responses=(
            '{"call_to_action":"Try the door.","output_type":"free",'
            '"options":[],"tag":"action"}',
            resolution_text
            or '{"event_text":"The lock holds, and the noise alerts the guard.",'
            '"boundary":"none","visibility":"participants"}',
        ),
        choice_responses=("actor-a", "none"),
    )
    factory = ConcordiaActorFactory({"actor": actor_model, "gm": gm_model})
    actor = factory.build_actor(
        default_character_recipe(
            model_profile_id="actor",
            content_locale="en-US",
        ),
        actor_params={
            "name": "actor-a",
            "identity": "An impatient detective.",
            "project_root": ".",
            "branch_id": "main",
        },
        memory=ConcordiaMemoryBank(
            owner_id="actor-a",
            scope=MemoryScope.CHARACTER,
        ),
    )
    gm_memory = ConcordiaMemoryBank(
        owner_id="gm",
        scope=MemoryScope.GAME_MASTER,
    )
    gm = factory.build_game_master(
        default_game_master_recipe(
            model_profile_id="gm",
            content_locale="en-US",
        ),
        gm_params={
            "name": "gm",
            "scene_goal": "Enter the archive.",
            "project_root": ".",
            "branch_id": "main",
        },
        actors=(actor,),
        shared_memory=gm_memory,
    )
    return actor, gm, gm_memory


def test_resolver_separates_putative_action_from_world_event() -> None:
    actor, gm, gm_memory = _runtime()
    selected = gm.select_next_actor((actor,), session_id="session-1", step=0)  # type: ignore[attr-defined]
    spec = gm.create_action_spec(  # type: ignore[attr-defined]
        actor,
        session_id="session-1",
        step=0,
        content_locale="en-US",
    )
    action = actor.act(spec)  # type: ignore[attr-defined]

    result = ConcordiaResolverKernel().resolve(
        gm,  # type: ignore[arg-type]
        ResolverContext(
            session_id="session-1",
            branch_id="main",
            step=0,
            acting_actor_id=selected,
            putative_event_text=action,
            content_locale="en-US",
        ),
        cancellation=Event(),
    )
    records = gm_memory.retrieve_recent(limit=4)

    assert result.putative_event_text == "I force the locked door."
    assert "lock holds" in result.raw_resolution_text
    assert tuple(record.record_type for record in records) == (
        MemoryRecordType.PUTATIVE_EVENT,
        MemoryRecordType.WORLD_EVENT,
    )
    assert records[1].source_record_ids == (records[0].record_id,)


def test_cancelled_resolution_does_not_write_memory() -> None:
    _, gm, gm_memory = _runtime()
    cancellation = Event()
    cancellation.set()

    try:
        ConcordiaResolverKernel().resolve(
            gm,  # type: ignore[arg-type]
            ResolverContext(
                session_id="session-1",
                branch_id="main",
                step=0,
                acting_actor_id="actor-a",
                putative_event_text="I force the door.",
                content_locale="en-US",
            ),
            cancellation=cancellation,
        )
    except SimulationCancelledError:
        pass
    else:
        raise AssertionError("cancelled resolution must fail")

    assert gm_memory.snapshot().record_count == 0


def test_invalid_resolution_envelope_fails_without_writing_memory() -> None:
    actor, gm, gm_memory = _runtime(
        resolution_text=(
            '{"event_text":"The lock holds.",'
            '"entity_changes":[{"operation":"create_npc","entity_id":"npc-1",'
            '"display_name":"Npc","identity":"x","core_desire":"y",'
            '"location":"z",'
            '"unexpected_field":true}]}'
        )
    )

    try:
        gm.select_next_actor((actor,), session_id="session-1", step=0)  # type: ignore[attr-defined]
        gm.create_action_spec(  # type: ignore[attr-defined]
            actor,
            session_id="session-1",
            step=0,
            content_locale="en-US",
        )
        ConcordiaResolverKernel().resolve(
            gm,  # type: ignore[arg-type]
            ResolverContext(
                session_id="session-1",
                branch_id="main",
                step=0,
                acting_actor_id=actor.name,  # type: ignore[attr-defined]
                putative_event_text="I force the door.",
                content_locale="en-US",
            ),
            cancellation=Event(),
        )
    except ResolutionEnvelopeError:
        pass
    else:
        raise AssertionError("invalid resolution envelope must fail")

    record_types = tuple(
        record.record_type
        for record in gm_memory.retrieve_recent(limit=5)
    )
    assert record_types == (MemoryRecordType.PUTATIVE_EVENT,)


def test_resolution_envelope_maps_entity_changes_to_effects() -> None:
    actor, gm, _ = _runtime(
        resolution_text=(
            '{"event_text":"A new figure enters the archive.",'
            '"boundary":"none","visibility":"public",'
            '"observer_ids":["actor-a"],"participant_ids":["actor-a","npc-1"],'
            '"entity_changes":[{"operation":"create_npc","entity_id":"npc-1",'
            '"display_name":"New Figure","identity":"A quiet archivist.",'
            '"core_desire":"Protect the records.","location":"archive"}]}'
        )
    )

    selected = gm.select_next_actor(  # type: ignore[attr-defined]
        (actor,),
        session_id="session-1",
        step=0,
    )
    gm.create_action_spec(  # type: ignore[attr-defined]
        actor,
        session_id="session-1",
        step=0,
        content_locale="en-US",
    )
    result = ConcordiaResolverKernel().resolve(
        gm,  # type: ignore[arg-type]
        ResolverContext(
            session_id="session-1",
            branch_id="main",
            step=0,
            acting_actor_id=selected,
            putative_event_text="I force the door.",
            content_locale="en-US",
        ),
        cancellation=Event(),
    )

    assert result.events[0].event_text == "A new figure enters the archive."
    assert result.effects[0].operation.value == "create_character"
    assert result.effects[0].target_id == "npc-1"
    assert result.effects[0].after is not None
    assert result.effects[0].after["display_name"] == "New Figure"  # type: ignore[index]
