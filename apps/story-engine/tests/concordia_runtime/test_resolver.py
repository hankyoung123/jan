from threading import Event

import pytest

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
from story_engine.domain.projection import ResolutionEnvelope
from story_engine.domain.simulation import CharacterRef, ResolverContext


def _character_ref(
    character_id: str = "actor-a",
    *,
    display_name: str | None = None,
    type: str = "active",
    location: str | None = None,
) -> CharacterRef:
    return CharacterRef(
        id=character_id,
        display_name=display_name or character_id,
        type=type,  # type: ignore[arg-type]
        location=location,
    )


def _runtime(
    resolution_text: str | None = None,
    *,
    direct_human_resolution: bool = False,
) -> tuple[object, object, ConcordiaMemoryBank]:
    actor_model = ReplayLanguageModel(text_responses=("I force the locked door.",))
    resolved_text = (
        resolution_text
        or '{"event_text":"The lock holds, and the noise alerts the guard.",'
        '"boundary":"none","visibility":"participants"}'
    )
    gm_model = ReplayLanguageModel(
        text_responses=(
            (resolved_text,)
            if direct_human_resolution
            else (
                '{"call_to_action":"Try the door.","output_type":"free",'
                '"options":[],"tag":"action"}',
                resolved_text,
            )
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


def _resolve(
    resolution_text: str,
    *,
    existing_characters: tuple[CharacterRef, ...] | None = None,
):
    actor, gm, gm_memory = _runtime(resolution_text=resolution_text)
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
            existing_characters=existing_characters or (_character_ref(),),
        ),
        cancellation=Event(),
    )
    return result, gm_memory


def test_resolution_schema_keeps_npc_identity_semantic() -> None:
    schema = ResolutionEnvelope.model_json_schema()
    assert set(schema["properties"]) == {
        "event_text",
        "boundary",
        "visibility",
        "observer_names",
        "participant_names",
        "entity_changes",
        "state_updates",
    }
    entity = schema["$defs"]["EntityChange"]
    assert set(entity["properties"]) == {
        "display_name",
        "identity",
        "core_desire",
        "location",
    }
    assert "entity_id" not in entity["properties"]
    assert "operation" not in entity["properties"]
    state_update = schema["$defs"]["ResolutionStateUpdate"]
    assert set(state_update["properties"]) == {
        "target",
        "target_name",
        "path",
        "value",
    }
    assert "target_id" not in state_update["properties"]


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
            existing_characters=(_character_ref(),),
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


def test_direct_human_intent_sets_concordia_active_actor_before_resolution() -> None:
    actor, gm, _ = _runtime(direct_human_resolution=True)
    gm.set_active_actor(actor.name)  # type: ignore[attr-defined]

    result = ConcordiaResolverKernel().resolve(
        gm,  # type: ignore[arg-type]
        ResolverContext(
            session_id="session-1",
            branch_id="main",
            step=0,
            acting_actor_id=actor.name,  # type: ignore[attr-defined]
            putative_event_text="I force the door.",
            content_locale="en-US",
            existing_characters=(_character_ref(),),
        ),
        cancellation=Event(),
    )

    assert result.raw_resolution_text.startswith("The lock holds")


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
                existing_characters=(_character_ref(),),
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
            '"boundary":"none","visibility":"participants",'
            '"entity_changes":[{"display_name":"Npc",'
            '"identity":"x","core_desire":"y",'
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
                existing_characters=(_character_ref(),),
            ),
            cancellation=Event(),
        )
    except ResolutionEnvelopeError:
        pass
    else:
        raise AssertionError("invalid resolution envelope must fail")

    record_types = tuple(
        record.record_type for record in gm_memory.retrieve_recent(limit=5)
    )
    assert record_types == (MemoryRecordType.PUTATIVE_EVENT,)


def test_resolution_envelope_maps_entity_changes_to_effects() -> None:
    actor, gm, _ = _runtime(
        resolution_text=(
            '{"event_text":"A new figure enters the archive.",'
            '"boundary":"none","visibility":"public",'
            '"observer_names":["actor-a"],"participant_names":["actor-a"],'
            '"entity_changes":[{"display_name":"New Figure",'
            '"identity":"A quiet archivist.",'
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
            existing_characters=(_character_ref(),),
        ),
        cancellation=Event(),
    )

    assert result.events[0].event_text == "A new figure enters the archive."
    assert result.effects[0].operation.value == "create_character"
    assert result.effects[0].target_id == "new-figure"
    assert result.effects[0].after is not None
    assert result.effects[0].after["display_name"] == "New Figure"  # type: ignore[index]


def test_resolution_envelope_binds_state_update_names_to_local_ids() -> None:
    actor, gm, _ = _runtime(
        resolution_text=(
            '{"event_text":"The actor reaches the hall.",'
            '"boundary":"none","visibility":"participants",'
            '"participant_names":["actor-a"],'
            '"state_updates":[{"target":"character_projection",'
            '"target_name":"actor-a","path":"location",'
            '"value":"hall"}]}'
        )
    )
    selected = gm.select_next_actor(  # type: ignore[attr-defined]
        (actor,), session_id="session-1", step=0
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
            putative_event_text="I walk to the hall.",
            content_locale="en-US",
            existing_characters=(_character_ref(),),
        ),
        cancellation=Event(),
    )

    assert len(result.effects) == 1
    assert result.effects[0].target_id == "actor-a"
    assert result.effects[0].path == "location"
    assert result.effects[0].after == "hall"


def test_duplicate_create_npc_with_identical_content_creates_once() -> None:
    entity_change = (
        '{"display_name":"New Figure","identity":"A quiet archivist.",'
        '"core_desire":"Protect the records.","location":"archive"}'
    )
    result, _ = _resolve(
        '{"event_text":"A new figure enters.","boundary":"none",'
        '"visibility":"participants","participant_names":["actor-a"],'
        f'"entity_changes":[{entity_change},{entity_change}]}}'
    )

    assert len(result.effects) == 1
    assert result.effects[0].target_id == "new-figure"


def test_duplicate_create_npc_with_conflicting_content_is_rejected() -> None:
    resolution = (
        '{"event_text":"A figure enters.","boundary":"none",'
        '"visibility":"participants","participant_names":["actor-a"],'
        '"entity_changes":['
        '{"display_name":"New Figure","identity":"A quiet archivist.",'
        '"core_desire":"Protect the records."},'
        '{"display_name":"New Figure","identity":"A harbor guard.",'
        '"core_desire":"Protect the records."}]}'
    )
    actor, gm, gm_memory = _runtime(resolution_text=resolution)
    selected = gm.select_next_actor(  # type: ignore[attr-defined]
        (actor,), session_id="session-1", step=0
    )
    gm.create_action_spec(  # type: ignore[attr-defined]
        actor,
        session_id="session-1",
        step=0,
        content_locale="en-US",
    )

    with pytest.raises(ResolutionEnvelopeError, match="conflicting create_npc"):
        ConcordiaResolverKernel().resolve(
            gm,  # type: ignore[arg-type]
            ResolverContext(
                session_id="session-1",
                branch_id="main",
                step=0,
                acting_actor_id=selected,
                putative_event_text="I force the door.",
                content_locale="en-US",
                existing_characters=(_character_ref(),),
            ),
            cancellation=Event(),
        )

    assert tuple(
        record.record_type for record in gm_memory.retrieve_recent(limit=5)
    ) == (MemoryRecordType.PUTATIVE_EVENT,)


def test_existing_character_create_is_converted_to_participant_reference() -> None:
    result, _ = _resolve(
        '{"event_text":"The archivist answers.","boundary":"none",'
        '"visibility":"participants","participant_names":["actor-a"],'
        '"entity_changes":[{"display_name":"New Figure",'
        '"identity":"A quiet archivist.",'
        '"core_desire":"Protect the records."}]}',
        existing_characters=(
            _character_ref(),
            _character_ref(
                "npc-1",
                display_name="New Figure",
                type="npc",
                location="archive",
            ),
        ),
    )

    assert result.effects == ()
    assert result.events[0].participant_ids == ("actor-a", "npc-1")


def test_differently_named_entity_gets_a_new_local_id() -> None:
    resolution = (
        '{"event_text":"A stranger enters.","boundary":"none",'
        '"visibility":"participants","participant_names":["actor-a"],'
        '"entity_changes":[{"display_name":"Different Person",'
        '"identity":"A stranger.",'
        '"core_desire":"Enter the archive."}]}'
    )
    actor, gm, gm_memory = _runtime(resolution_text=resolution)
    selected = gm.select_next_actor(  # type: ignore[attr-defined]
        (actor,), session_id="session-1", step=0
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
            existing_characters=(
                _character_ref(),
                _character_ref(
                    "npc-1",
                    display_name="New Figure",
                    type="npc",
                ),
            ),
        ),
        cancellation=Event(),
    )
    assert result.effects[0].target_id == "different-person"

    assert tuple(
        record.record_type for record in gm_memory.retrieve_recent(limit=5)
    ) == (
        MemoryRecordType.PUTATIVE_EVENT,
        MemoryRecordType.WORLD_EVENT,
    )
