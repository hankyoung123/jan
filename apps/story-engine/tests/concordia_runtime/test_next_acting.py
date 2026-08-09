import json
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from story_engine.concordia_runtime.factory import (
    ConcordiaActorFactory,
    default_character_recipe,
    default_game_master_recipe,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.replay import ReplayLanguageModel
from story_engine.domain.action import ActionOutputType
from story_engine.domain.memory import MemoryScope


class FirstChoiceModel(ReplayLanguageModel):
    """Records the choice schema while selecting its first allowed option."""

    def __init__(self) -> None:
        super().__init__()
        self.choice_options: list[tuple[str, ...]] = []

    def sample_choice(
        self,
        prompt: str,
        responses: Sequence[str],
        *,
        seed: int | None = None,
    ) -> tuple[int, str, Mapping[str, Any]]:
        del prompt, seed
        self.choice_options.append(tuple(responses))
        return 0, responses[0], {"source": "first-choice"}


@pytest.mark.parametrize(
    ("envelope", "expected_type"),
    [
        (
            {
                "call_to_action": "Answer the witness.",
                "output_type": "free",
                "options": [],
                "tag": "dialogue",
            },
            ActionOutputType.FREE,
        ),
        (
            {
                "call_to_action": "Where do you go?",
                "output_type": "choice",
                "options": ["home", "London"],
                "tag": "story_action",
            },
            ActionOutputType.CHOICE,
        ),
    ],
)
def test_game_master_maps_semantic_actor_name_to_local_id(
    envelope: dict[str, Any],
    expected_type: ActionOutputType,
) -> None:
    actor_model = ReplayLanguageModel()
    gm_model = ReplayLanguageModel(choice_responses=("Actor B",))
    action_spec_model = ReplayLanguageModel(
        text_responses=(json.dumps(envelope, ensure_ascii=False),),
    )
    factory = ConcordiaActorFactory({"actor": actor_model, "gm": gm_model})
    actors = tuple(
        factory.build_actor(
            default_character_recipe(
                model_profile_id="actor",
                content_locale="en-US",
            ),
            actor_params={
                "name": actor_id,
                "display_name": display_name,
                "identity": f"Identity of {display_name}.",
                "project_root": ".",
                "branch_id": "main",
            },
            memory=ConcordiaMemoryBank(
                owner_id=actor_id,
                scope=MemoryScope.CHARACTER,
            ),
        )
        for actor_id, display_name in (("actor-a", "Actor A"), ("actor-b", "Actor B"))
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
        component_models={"next_action_spec": action_spec_model},
    )

    selected = gm.select_next_actor(
        actors,
        session_id="session-1",
        step=0,
    )
    action_spec = gm.create_action_spec(
        actors[1],
        session_id="session-1",
        step=0,
        content_locale="en-US",
    )

    assert selected == "actor-b"
    assert action_spec.output_type == expected_type
    assert action_spec.call_to_action == envelope["call_to_action"]
    assert action_spec.tag == envelope["tag"]
    if expected_type == ActionOutputType.CHOICE:
        assert action_spec.options == tuple(envelope["options"])


def test_game_master_uses_only_eligible_actors_for_next_turn() -> None:
    actor_model = ReplayLanguageModel()
    gm_model = FirstChoiceModel()
    factory = ConcordiaActorFactory({"actor": actor_model, "gm": gm_model})
    actors = tuple(
        factory.build_actor(
            default_character_recipe(
                model_profile_id="actor",
                content_locale="zh-CN",
            ),
            actor_params={
                "name": actor_id,
                "display_name": display_name,
                "identity": f"{display_name} 的身份。",
                "project_root": ".",
                "branch_id": "main",
            },
            memory=ConcordiaMemoryBank(
                owner_id=actor_id,
                scope=MemoryScope.CHARACTER,
            ),
        )
        for actor_id, display_name in (("player", "你"), ("zhang-ye", "张野"))
    )
    gm = factory.build_game_master(
        default_game_master_recipe(
            model_profile_id="gm",
            content_locale="zh-CN",
        ),
        gm_params={
            "name": "gm",
            "scene_goal": "调查港口。",
            "project_root": ".",
            "branch_id": "main",
        },
        actors=actors,
        shared_memory=ConcordiaMemoryBank(
            owner_id="gm",
            scope=MemoryScope.GAME_MASTER,
        ),
    )

    selected = gm.select_next_actor(
        (actors[1],),
        session_id="session-1",
        step=1,
    )

    assert selected == "zhang-ye"
    assert gm_model.choice_options == [("a",)]
