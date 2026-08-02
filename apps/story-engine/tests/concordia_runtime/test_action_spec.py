import json

import pytest
from concordia.typing import entity as concordia_entity  # type: ignore[import-untyped]

from story_engine.concordia_runtime.action_spec import (
    ActionSpecDecodeError,
    ConcordiaActionSpecCodec,
)
from story_engine.domain.action import ActionOutputType, ActionSpec, StoryActionKind


@pytest.mark.parametrize(
    "output_type",
    [ActionOutputType.FREE, ActionOutputType.CHOICE],
)
def test_action_spec_round_trip(output_type: ActionOutputType) -> None:
    options = ("wait", "leave") if output_type == ActionOutputType.CHOICE else ()
    spec = ActionSpec(
        spec_id="spec:1",
        output_type=output_type,
        call_to_action="What happens next?",
        options=options,
        tag="story_action",
        content_locale="en-US",
    )

    decoded = ConcordiaActionSpecCodec.from_json(
        ConcordiaActionSpecCodec.to_json(spec),
        spec_id=spec.spec_id,
        content_locale=spec.content_locale,
    )

    assert decoded.output_type == spec.output_type
    assert decoded.call_to_action == spec.call_to_action
    assert decoded.options == spec.options
    assert decoded.tag == spec.tag


def test_skip_action_spec_normalizes_empty_concordia_prompt() -> None:
    decoded = ConcordiaActionSpecCodec.from_concordia(
        concordia_entity.skip_this_step_action_spec(),
        spec_id="spec:skip",
        content_locale="en-US",
    )

    assert decoded.output_type == ActionOutputType.SKIP_THIS_STEP
    assert decoded.action_kind == StoryActionKind.WAIT
    assert decoded.call_to_action


def test_action_spec_infers_dialogue_and_wait_kinds_from_gm_tags() -> None:
    dialogue = ConcordiaActionSpecCodec.from_json(
        json.dumps(
            {
                "call_to_action": "Answer the witness.",
                "output_type": "free",
                "options": [],
                "tag": "dialogue",
            }
        ),
        spec_id="spec:dialogue",
        content_locale="en-US",
    )
    wait = ConcordiaActionSpecCodec.from_json(
        json.dumps(
            {
                "call_to_action": "Wait and observe.",
                "output_type": "free",
                "options": [],
                "tag": "wait",
            }
        ),
        spec_id="spec:wait",
        content_locale="en-US",
    )

    assert dialogue.action_kind == StoryActionKind.DIALOGUE
    assert wait.action_kind == StoryActionKind.WAIT


def test_action_spec_rejects_bad_json_unknown_fields_and_actors() -> None:
    with pytest.raises(ActionSpecDecodeError, match="valid JSON"):
        ConcordiaActionSpecCodec.from_json(
            "{bad",
            spec_id="spec:1",
            content_locale="en-US",
        )

    payload = json.dumps(
        {
            "call_to_action": "Who acts?",
            "output_type": "next_acting",
            "options": ["actor-c"],
            "tag": "next_acting",
        }
    )
    with pytest.raises(ActionSpecDecodeError, match="unknown actors"):
        ConcordiaActionSpecCodec.from_json(
            payload,
            spec_id="spec:2",
            content_locale="en-US",
            actor_ids=("actor-a", "actor-b"),
        )
