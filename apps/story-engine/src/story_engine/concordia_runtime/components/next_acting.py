"""Turn-selection and action-spec components for the Story Game Master."""

from collections.abc import Sequence
from typing import cast

from concordia.components import (  # type: ignore[import-untyped]
    game_master as gm_components,
)
from concordia.document import interactive_document  # type: ignore[import-untyped]
from concordia.typing import entity as entity_lib  # type: ignore[import-untyped]

from story_engine.concordia_runtime.action_spec import (
    ConcordiaActionSpecCodec,
)


class EligibleNextActing(gm_components.next_acting.NextActing):  # type: ignore[misc]
    """Choose only from the actors eligible for the current step.

    Concordia's built-in component keeps a construction-time roster and ignores
    ``ActionSpec.options``. The runtime uses those options to constrain an
    automatic interactive response to NPCs, so they must also constrain the
    model's choice prompt.
    """

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        if action_spec.output_type != entity_lib.OutputType.NEXT_ACTING:
            return cast(str, super().pre_act(action_spec))
        eligible_actor_names: tuple[str, ...] = tuple(action_spec.options)
        if not eligible_actor_names:
            raise ValueError("next actor selection requires eligible actor options")
        unknown_actor_names = set(eligible_actor_names) - set(self._player_names)
        if unknown_actor_names:
            raise ValueError(
                "next actor selection includes actors outside the active roster: "
                f"{sorted(unknown_actor_names)}"
            )

        prompt = interactive_document.InteractiveDocument(self._model)
        component_states = "\n".join(
            self._component_pre_act_display(key) for key in self._components
        )
        prompt.statement(f"{component_states}\n")
        selected_index = cast(
            int,
            prompt.multiple_choice_question(
                question="Whose turn is next?",
                answers=eligible_actor_names,
            ),
        )
        selected = eligible_actor_names[selected_index]
        self._currently_active_player = selected
        return selected


class SchemaNextActionSpec(gm_components.next_acting.NextActionSpec):  # type: ignore[misc]
    """Normalize structured Game Master output before Concordia parses it.

    The action-spec model is constrained with ``ActionSpecEnvelope`` JSON
    Schema. The installed sequential engine parses the raw model string with
    ``action_spec_from_dict``, so the envelope is decoded and re-encoded through
    the shared codec first; local option IDs are never model-authored.
    """

    def __init__(
        self,
        model: object,
        player_names: Sequence[str],
        components: Sequence[str] = (),
        content_locale: str = "en-US",
        **kwargs: object,
    ) -> None:
        super().__init__(model, player_names, components, **kwargs)
        self._content_locale = content_locale

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        raw: str = super().pre_act(action_spec)
        if action_spec.output_type != entity_lib.OutputType.NEXT_ACTION_SPEC:
            return raw
        parsed = ConcordiaActionSpecCodec.from_json(
            raw,
            spec_id="next-action-spec",
            content_locale=self._content_locale,
        )
        return ConcordiaActionSpecCodec.to_json(parsed)
