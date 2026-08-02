from story_engine.concordia_runtime.factory import (
    ConcordiaActorFactory,
    default_character_recipe,
    default_game_master_recipe,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.replay import ReplayLanguageModel
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.memory import MemoryScope
from story_engine.domain.recipe import PerceptionFrame


def _frame(step: int) -> PerceptionFrame:
    return PerceptionFrame(
        frame_id=f"observation:session-1:{step}:actor-a",
        session_id="session-1",
        branch_id="main",
        actor_id="actor-a",
        step=step,
        content_locale="en-US",
        observation_text=f"Observation {step}",
    )


def _action_spec(step: int) -> ActionSpec:
    return ActionSpec(
        spec_id=f"action:session-1:{step}",
        output_type=ActionOutputType.FREE,
        call_to_action="What do you do?",
        content_locale="en-US",
    )


def test_actor_persists_for_ten_steps_and_restores_equivalent_state() -> None:
    responses = tuple(f"action {step}" for step in range(10))
    model = ReplayLanguageModel(text_responses=responses)
    memory = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
    )
    factory = ConcordiaActorFactory({"actor": model})
    recipe = default_character_recipe(
        model_profile_id="actor",
        content_locale="en-US",
    )
    actor = factory.build_actor(
        recipe,
        actor_params={"name": "actor-a", "identity": "A careful investigator."},
        memory=memory,
    )

    for step in range(5):
        actor.observe(_frame(step))
        assert actor.act(_action_spec(step)) == f"action {step}"
    entity_checkpoint = actor.get_state()
    memory_checkpoint = memory.snapshot()
    model_checkpoint = model.get_state()

    original_tail = []
    for step in range(5, 10):
        actor.observe(_frame(step))
        original_tail.append(actor.act(_action_spec(step)))

    restored_model = ReplayLanguageModel(text_responses=responses)
    restored_model.set_state(model_checkpoint)
    restored_memory = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
    )
    restored_memory.restore(memory_checkpoint)
    restored_factory = ConcordiaActorFactory({"actor": restored_model})
    restored_actor = restored_factory.build_actor(
        recipe,
        actor_params={"name": "actor-a", "identity": "A careful investigator."},
        memory=restored_memory,
        initial_state=entity_checkpoint,
    )
    restored_tail = []
    for step in range(5, 10):
        restored_actor.observe(_frame(step))
        restored_tail.append(restored_actor.act(_action_spec(step)))

    assert restored_tail == original_tail
    assert restored_actor is not actor
    assert restored_actor.get_phase().value == "ready"
    assert restored_memory.snapshot().record_count == 10


def test_game_master_selects_actor_and_generates_dynamic_action_spec() -> None:
    actor_model = ReplayLanguageModel()
    gm_model = ReplayLanguageModel(
        text_responses=(
            '{"call_to_action":"Answer the witness.","output_type":"free",'
            '"options":[],"tag":"dialogue"}',
        ),
        choice_responses=("actor-b", "No"),
    )
    factory = ConcordiaActorFactory({"actor": actor_model, "gm": gm_model})
    character_recipe = default_character_recipe(
        model_profile_id="actor",
        content_locale="en-US",
    )
    actors = tuple(
        factory.build_actor(
            character_recipe,
            actor_params={"name": actor_id, "identity": actor_id},
            memory=ConcordiaMemoryBank(
                owner_id=actor_id,
                scope=MemoryScope.CHARACTER,
            ),
        )
        for actor_id in ("actor-a", "actor-b")
    )
    gm = factory.build_game_master(
        default_game_master_recipe(
            model_profile_id="gm",
            content_locale="en-US",
        ),
        gm_params={"name": "gm", "scene_goal": "Question the witness."},
        actors=actors,
        shared_memory=ConcordiaMemoryBank(
            owner_id="gm",
            scope=MemoryScope.GAME_MASTER,
        ),
    )

    actor_id = gm.select_next_actor(actors, session_id="session-1", step=0)
    action_spec = gm.create_action_spec(
        actors[1],
        session_id="session-1",
        step=0,
        content_locale="en-US",
    )

    assert actor_id == "actor-b"
    assert action_spec.output_type == ActionOutputType.FREE
    assert action_spec.tag == "dialogue"
    assert gm.should_terminate(session_id="session-1", step=0) == (False, None)
