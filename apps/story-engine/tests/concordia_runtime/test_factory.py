import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from threading import Event
from typing import Any

from profile_factory import agent_profile as _profile

from story_engine.concordia_runtime.factory import (
    ConcordiaActorFactory,
    default_character_recipe,
    default_game_master_recipe,
)
from story_engine.concordia_runtime.language_model import JanConcordiaLanguageModel
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.replay import ReplayLanguageModel
from story_engine.concordia_runtime.resolver import ConcordiaResolverKernel
from story_engine.domain.action import (
    ActionOutputType,
    ActionSpec,
    ActionSpecEnvelope,
)
from story_engine.domain.memory import MemoryScope
from story_engine.domain.projection import ResolutionEnvelope
from story_engine.domain.recipe import PerceptionFrame
from story_engine.domain.simulation import CharacterRef, ResolverContext
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.gateway import ModelGateway
from story_engine.models.registry import ProfileRegistry


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
        actor_params={
            "name": "actor-a",
            "identity": "A careful investigator.",
            "project_root": ".",
            "branch_id": "main",
        },
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
        actor_params={
            "name": "actor-a",
            "identity": "A careful investigator.",
            "project_root": ".",
            "branch_id": "main",
        },
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
                actor_params={
                    "name": actor_id,
                    "identity": actor_id,
                    "project_root": ".",
                    "branch_id": "main",
                },
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
        gm_params={
            "name": "gm",
            "scene_goal": "Question the witness.",
            "project_root": ".",
            "branch_id": "main",
        },
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


class RecordingTransport:
    def __init__(self, responses: tuple[str, ...]) -> None:
        self._responses = list(responses)
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(dict(payload))
        return {
            "choices": [
                {
                    "message": {"content": self._responses.pop(0)},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[ModelStreamChunk]:
        del payload, timeout_seconds
        if False:
            yield ModelStreamChunk()
        raise AssertionError("factory schema tests do not stream")


def test_game_master_component_models_use_json_schema(tmp_path: Path) -> None:
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        _profile(
            id="gm",
            task_type="game_master",
            model_ref="test-provider/gm",
            max_output_tokens=4096,
        )
    )
    transport = RecordingTransport(
        (
            '{"choice":"a"}',
            '{"call_to_action":"Answer the witness.","output_type":"free",'
            '"options":[],"tag":"dialogue"}',
            '{"event_text":"The witness answers.","boundary":"none",'
            '"visibility":"participants"}',
        )
    )
    gateway = ModelGateway(registry, transport)
    actor_model = ReplayLanguageModel()
    shared_gm = JanConcordiaLanguageModel(
        gateway,
        profile_id="game_master",
        task_type="game_master",
        content_locale="en-US",
    )
    action_spec_model = JanConcordiaLanguageModel(
        gateway,
        profile_id="game_master",
        task_type="game_master",
        content_locale="en-US",
        output_schema=json.dumps(
            ActionSpecEnvelope.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    resolution_model = JanConcordiaLanguageModel(
        gateway,
        profile_id="game_master",
        task_type="game_master",
        content_locale="en-US",
        output_schema=json.dumps(
            ResolutionEnvelope.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )
    factory = ConcordiaActorFactory({"actor": actor_model, "gm": shared_gm})
    recipe = default_character_recipe(
        model_profile_id="actor",
        content_locale="en-US",
    )
    actor = factory.build_actor(
        recipe,
        actor_params={
            "name": "actor-a",
            "identity": "A careful investigator.",
            "project_root": ".",
            "branch_id": "main",
        },
        memory=ConcordiaMemoryBank(
            owner_id="actor-a",
            scope=MemoryScope.CHARACTER,
        ),
    )
    gm = factory.build_game_master(
        default_game_master_recipe(
            model_profile_id="gm",
            content_locale="en-US",
        ),
        gm_params={
            "name": "gm",
            "scene_goal": "Question the witness.",
            "project_root": ".",
            "branch_id": "main",
        },
        actors=(actor,),
        shared_memory=ConcordiaMemoryBank(
            owner_id="gm",
            scope=MemoryScope.GAME_MASTER,
        ),
        component_models={
            "next_action_spec": action_spec_model,
            "resolution": resolution_model,
        },
    )

    selected = gm.select_next_actor((actor,), session_id="session-1", step=0)
    spec = gm.create_action_spec(
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
            putative_event_text="I ask the witness a question.",
            content_locale="en-US",
            existing_characters=(
                CharacterRef(
                    id="actor-a",
                    display_name="Actor A",
                    type="active",
                    location="archive",
                ),
            ),
        ),
        cancellation=Event(),
    )

    assert spec.tag == "dialogue"
    assert result.events[0].event_text == "The witness answers."
    choice_call, spec_call, resolution_call = transport.calls
    assert choice_call["max_tokens"] == 4096
    assert spec_call["response_format"]["type"] == "json_schema"
    assert spec_call["max_tokens"] == 4096
    spec_schema = spec_call["response_format"]["json_schema"]["schema"]
    assert "call_to_action" in spec_schema["properties"]
    assert resolution_call["response_format"]["type"] == "json_schema"
    assert resolution_call["max_tokens"] == 4096
    resolution_schema = resolution_call["response_format"]["json_schema"]["schema"]
    assert "event_text" in resolution_schema["properties"]
    assert "entity_changes" in resolution_schema["properties"]
    resolution_prompt = "\n".join(
        message["content"] for message in resolution_call["messages"]
    )
    assert "Existing characters:" in resolution_prompt
    assert (
        "- actor-a: Actor A, active character, location: archive"
        in resolution_prompt
    )
    assert "must not appear in entity_changes" in resolution_prompt
