import json
from collections.abc import Mapping
from typing import ClassVar

from concordia.typing import entity as concordia_entity  # type: ignore[import-untyped]
from pydantic import ValidationError

from story_engine.domain.action import (
    ActionOutputType,
    ActionSpec,
    ActionSpecEnvelope,
    StoryActionKind,
)


class ActionSpecDecodeError(ValueError):
    """Raised when a Game Master returns an invalid action specification."""


class ConcordiaActionSpecCodec:
    _KINDS_BY_TAG: ClassVar[Mapping[str, StoryActionKind]] = {
        "action": StoryActionKind.FREE_ACTION,
        "dialogue": StoryActionKind.DIALOGUE,
        "reaction": StoryActionKind.REACTION,
        "internal": StoryActionKind.INTERNAL_DECISION,
        "internal_decision": StoryActionKind.INTERNAL_DECISION,
        "choice": StoryActionKind.CHOICE,
        "wait": StoryActionKind.WAIT,
        "scene_proposal": StoryActionKind.SCENE_PROPOSAL,
    }

    @staticmethod
    def to_concordia(spec: ActionSpec) -> concordia_entity.ActionSpec:
        return concordia_entity.ActionSpec(
            call_to_action=spec.call_to_action,
            output_type=concordia_entity.OutputType(spec.output_type.value),
            options=spec.options,
            tag=spec.tag,
        )

    @classmethod
    def from_concordia(
        cls,
        spec: concordia_entity.ActionSpec,
        *,
        spec_id: str,
        content_locale: str,
        action_kind: StoryActionKind | None = None,
        option_ids: tuple[str, ...] | None = None,
    ) -> ActionSpec:
        call_to_action = spec.call_to_action or "Skip this simulation step."
        inferred_kind = action_kind or cls._KINDS_BY_TAG.get(spec.tag or "")
        if (
            inferred_kind is None
            and spec.output_type == concordia_entity.OutputType.SKIP_THIS_STEP
        ):
            inferred_kind = StoryActionKind.WAIT
        try:
            return ActionSpec(
                spec_id=spec_id,
                output_type=ActionOutputType(spec.output_type.value),
                action_kind=inferred_kind,
                call_to_action=call_to_action,
                options=tuple(spec.options),
                tag=spec.tag,
                content_locale=content_locale,
                option_ids=tuple(option_ids) if option_ids else (),
            )
        except ValidationError as error:
            raise ActionSpecDecodeError("invalid Concordia ActionSpec") from error

    @classmethod
    def from_json(
        cls,
        value: str,
        *,
        spec_id: str,
        content_locale: str,
        actor_ids: tuple[str, ...] | None = None,
    ) -> ActionSpec:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError as error:
            raise ActionSpecDecodeError("ActionSpec must be valid JSON") from error
        if not isinstance(payload, dict):
            raise ActionSpecDecodeError("ActionSpec JSON must be an object")
        allowed = {"call_to_action", "output_type", "options", "option_ids", "tag"}
        if set(payload) - allowed:
            raise ActionSpecDecodeError("ActionSpec JSON contains unknown fields")
        try:
            envelope = ActionSpecEnvelope.model_validate(payload)
        except ValidationError as error:
            location = ".".join(str(item) for item in error.errors()[0]["loc"])
            raise ActionSpecDecodeError(
                f"ActionSpec JSON has invalid fields at {location}"
            ) from error
        concordia_spec = concordia_entity.ActionSpec(
            call_to_action=envelope.call_to_action,
            output_type=concordia_entity.OutputType(envelope.output_type.value),
            options=list(envelope.options),
            tag=envelope.tag,
        )
        spec = cls.from_concordia(
            concordia_spec,
            spec_id=spec_id,
            content_locale=content_locale,
            option_ids=tuple(envelope.option_ids) if envelope.option_ids else None,
        )
        if spec.output_type == ActionOutputType.NEXT_ACTING:
            if actor_ids is None:
                raise ActionSpecDecodeError(
                    "NEXT_ACTING validation requires available actor IDs"
                )
            unknown = set(spec.options) - set(actor_ids)
            if unknown:
                raise ActionSpecDecodeError(
                    f"ActionSpec references unknown actors: {sorted(unknown)}"
                )
        return spec

    @staticmethod
    def to_json(spec: ActionSpec) -> str:
        concordia_spec = ConcordiaActionSpecCodec.to_concordia(spec)
        return json.dumps(
            concordia_spec.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
