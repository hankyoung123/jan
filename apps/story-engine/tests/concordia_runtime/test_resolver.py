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
    SimulationCancelledError,
)
from story_engine.domain.memory import MemoryRecordType, MemoryScope
from story_engine.domain.simulation import ResolverContext


def _runtime() -> tuple[object, object, ConcordiaMemoryBank]:
    actor_model = ReplayLanguageModel(text_responses=("I force the locked door.",))
    gm_model = ReplayLanguageModel(
        text_responses=(
            '{"call_to_action":"Try the door.","output_type":"free",'
            '"options":[],"tag":"action"}',
            "The lock holds, and the noise alerts the guard.",
        ),
        choice_responses=("actor-a", "none"),
    )
    factory = ConcordiaActorFactory({"actor": actor_model, "gm": gm_model})
    actor = factory.build_actor(
        default_character_recipe(
            model_profile_id="actor",
            content_locale="en-US",
        ),
        actor_params={"name": "actor-a", "identity": "An impatient detective."},
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
        gm_params={"name": "gm", "scene_goal": "Enter the archive."},
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
