import json
from typing import Any

import pytest
from concordia.environment.engines import sequential  # type: ignore[import-untyped]
from concordia.typing import entity as concordia_entity  # type: ignore[import-untyped]

from story_engine.concordia_runtime.factory import (
    ConcordiaActorFactory,
    default_character_recipe,
    default_game_master_recipe,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.replay import ReplayLanguageModel
from story_engine.domain.memory import MemoryScope


@pytest.mark.parametrize(
    ("envelope", "expected_type"),
    [
        (
            {
                "call_to_action": "Answer the witness.",
                "output_type": "free",
                "options": [],
                "option_ids": [],
                "tag": "dialogue",
            },
            concordia_entity.OutputType.FREE,
        ),
        (
            {
                "call_to_action": "Where do you go?",
                "output_type": "choice",
                "options": ["home", "London"],
                "option_ids": ["home-opt", "london-opt"],
                "tag": "story_action",
            },
            concordia_entity.OutputType.CHOICE,
        ),
    ],
)
def test_sequential_next_acting_accepts_envelope_with_option_ids(
    envelope: dict[str, Any],
    expected_type: concordia_entity.OutputType,
) -> None:
    actor_model = ReplayLanguageModel()
    gm_model = ReplayLanguageModel(choice_responses=("actor-b",))
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
                "identity": f"Identity of {actor_id}.",
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
        component_models={"next_action_spec": action_spec_model},
    )

    raw_actor, raw_spec = sequential.Sequential().next_acting(
        gm.entity,
        tuple(actor.entity for actor in actors),
    )

    assert raw_actor.name == "actor-b"
    assert raw_spec.output_type == expected_type
    assert raw_spec.call_to_action == envelope["call_to_action"]
    assert raw_spec.tag == envelope["tag"]
    if expected_type == concordia_entity.OutputType.CHOICE:
        assert tuple(raw_spec.options) == tuple(envelope["options"])
