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
from story_engine.domain.simulation import ActorStateContext, ResolverContext
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.gateway import ModelGateway, ModelPartSink
from story_engine.models.registry import ProfileRegistry
from story_engine.submission.service import (
    SubmissionService,
    last_ferry_before_submission,
)


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
    assert "__memory__" not in entity_checkpoint["context_components"]
    memory_checkpoint = memory.records()
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
    restored_memory.replay(memory_checkpoint)
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
    assert len(restored_memory.records()) == 10


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
        first_content_timeout_seconds: float | None = None,
        part_sink: ModelPartSink | None = None,
    ) -> Mapping[str, Any]:
        del timeout_seconds, first_content_timeout_seconds
        self.calls.append(dict(payload))
        content = self._responses.pop(0)
        if part_sink is not None:
            part_sink("text", content)
        return {
            "choices": [
                {
                    "message": {"content": content},
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
                ActorStateContext(
                    id="actor-a",
                    display_name="Actor A",
                    type="active",
                    identity="A careful investigator.",
                    current_goal="Question the witness.",
                    location="archive",
                    capabilities=("Interviewing", "Photography"),
                    conditions=("Injured right hand",),
                    resources=("Camera", "Press card"),
                    beliefs=("The witness is withholding evidence.",),
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
    assert '"identity":"A careful investigator."' in resolution_prompt
    assert '"current_goal":"Question the witness."' in resolution_prompt
    assert '"location":"archive"' in resolution_prompt
    assert '"capabilities":["Interviewing","Photography"]' in resolution_prompt
    assert '"conditions":["Injured right hand"]' in resolution_prompt
    assert '"resources":["Camera","Press card"]' in resolution_prompt
    assert '"beliefs":["The witness is withholding evidence."]' in resolution_prompt
    assert "Use these exact display names" not in resolution_prompt
    assert "participant_names" not in resolution_prompt
    assert "Current Intent:\nI ask the witness a question." in resolution_prompt


def test_actor_prompt_excludes_canonical_facts_the_actor_does_not_know(
    tmp_path: Path,
) -> None:
    snapshot = SubmissionService(tmp_path).finalize(
        last_ferry_before_submission()
    )
    player = next(
        character for character in snapshot.characters if character.id == "player"
    )
    model = ReplayLanguageModel(text_responses=("I keep watching the lobby.",))
    actor = ConcordiaActorFactory({"actor": model}).build_actor(
        default_character_recipe(
            model_profile_id="actor",
            content_locale="zh-CN",
        ),
        actor_params={
            "name": player.id,
            "display_name": player.display_name or player.id,
            "actor_state": ActorStateContext.from_character(player).prompt_text(),
            "project_root": str(tmp_path / snapshot.project.id),
            "branch_id": "main",
        },
        memory=ConcordiaMemoryBank(
            owner_id=player.id,
            scope=MemoryScope.CHARACTER,
        ),
    )

    actor.act(
        ActionSpec(
            spec_id="action:player:1",
            output_type=ActionOutputType.FREE,
            call_to_action="你接下来做什么?",
            content_locale="zh-CN",
        )
    )

    prompt = model.prompts[-1]
    assert "你收到一条署名林澈、约你到旅馆的消息" in prompt
    assert "张野借用林澈遗失的旧手机发出了那条消息" not in prompt
    assert "港口事故并非林澈造成" not in prompt


def test_default_character_recipe_has_one_exact_intent_authority() -> None:
    assert default_character_recipe().system_instruction_text == (
        "你就是这个角色。\n\n"
        "只依据你的状态、记忆和当前感知行动。\n"
        "只决定自己的意图、行动和语言，不决定结果或他人的行为。\n"  # noqa: RUF001
        "不要使用你没有的知识、能力、物品或资源。\n"
        "做当前最自然的下一步，不解释规则或剧情。"  # noqa: RUF001
    )


def test_restored_character_uses_current_recipe_authority() -> None:
    recipe = default_character_recipe(
        model_profile_id="actor",
        content_locale="zh-CN",
    )
    first_model = ReplayLanguageModel()
    first = ConcordiaActorFactory({"actor": first_model}).build_actor(
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
    legacy_state = first.get_state()
    legacy_state["context_components"]["system_instruction"]["state"] = (
        "LEGACY_CHARACTER_AUTHORITY"
    )
    restored_model = ReplayLanguageModel(text_responses=("我继续观察。",))
    restored = ConcordiaActorFactory({"actor": restored_model}).build_actor(
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
        initial_state=legacy_state,
    )

    restored.act(
        ActionSpec(
            spec_id="action:actor-a:0",
            output_type=ActionOutputType.FREE,
            call_to_action="你接下来做什么?",
            content_locale="zh-CN",
        )
    )

    prompt = restored_model.prompts[-1]
    assert recipe.system_instruction_text in prompt
    assert "LEGACY_CHARACTER_AUTHORITY" not in prompt


def test_perception_call_receives_only_actor_specific_context() -> None:
    actor_model = ReplayLanguageModel()
    gm_model = ReplayLanguageModel(text_responses=("The rain hits the window.",))
    factory = ConcordiaActorFactory({"actor": actor_model, "gm": gm_model})
    actor = factory.build_actor(
        default_character_recipe(model_profile_id="actor", content_locale="en-US"),
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
            "scene_goal": "PLOT_GOAL_MUST_NOT_REACH_PERCEPTION",
            "pacing": "PACING_MUST_NOT_REACH_PERCEPTION",
            "project_root": ".",
            "branch_id": "main",
        },
        actors=(actor,),
        shared_memory=ConcordiaMemoryBank(
            owner_id="gm",
            scope=MemoryScope.GAME_MASTER,
        ),
    )

    frame = gm.make_observation(
        actor,
        session_id="session-1",
        step=0,
        content_locale="en-US",
        context_text="PERCEPTION_CONTEXT_ONLY",
    )

    assert frame.observation_text == "The rain hits the window."
    prompt = gm_model.prompts[-1]
    assert "PERCEPTION_CONTEXT_ONLY" in prompt
    assert "PLOT_GOAL_MUST_NOT_REACH_PERCEPTION" not in prompt
    assert "PACING_MUST_NOT_REACH_PERCEPTION" not in prompt
    assert "World Wiki" not in prompt
    assert "Resolved world events" not in prompt
    assert "Maintain shared world truth" not in prompt
