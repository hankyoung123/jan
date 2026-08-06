import pytest
from concordia.language_model import language_model  # type: ignore[import-untyped]

from story_engine.concordia_runtime.replay import (
    ReplayExhaustedError,
    ReplayLanguageModel,
)


def test_replay_model_is_deterministic_and_restorable() -> None:
    model = ReplayLanguageModel(
        text_responses=("first\nignored", "second"),
        choice_responses=("actor-b",),
    )

    assert model.sample_text("step one", terminators=("\n",)) == "first"
    checkpoint = model.get_state()
    assert model.sample_text("step two", terminators=()) == "second"
    assert model.sample_choice("who acts", ("actor-a", "actor-b"))[1] == "actor-b"

    model.set_state(checkpoint)

    assert model.sample_text("step two replay", terminators=()) == "second"
    assert model.sample_choice("who acts replay", ("actor-a", "actor-b"))[1] == (
        "actor-b"
    )
    assert model.prompts == ("step one", "step two replay", "who acts replay")


def test_replay_model_rejects_exhaustion_and_invalid_choice() -> None:
    empty = ReplayLanguageModel()
    with pytest.raises(ReplayExhaustedError, match="text response"):
        empty.sample_text("no response")

    invalid = ReplayLanguageModel(choice_responses=("missing",))
    with pytest.raises(language_model.InvalidResponseError):
        invalid.sample_choice("choose", ("actor-a", "actor-b"))
