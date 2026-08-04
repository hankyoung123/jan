"""Schema-constrained NextActionSpec component for the Story Game Master."""

from collections.abc import Sequence

from concordia.components import (  # type: ignore[import-untyped]
    game_master as gm_components,
)
from concordia.typing import entity as entity_lib  # type: ignore[import-untyped]

from story_engine.concordia_runtime.action_spec import (
    ConcordiaActionSpecCodec,
)


class SchemaNextActionSpec(gm_components.next_acting.NextActionSpec):  # type: ignore[misc]
    """Normalize structured Game Master output before Concordia parses it.

    The action-spec model is constrained with ``ActionSpecEnvelope`` JSON
    Schema, which also carries local-only fields such as ``option_ids``. The
    installed sequential engine parses the raw model string with
    ``action_spec_from_dict`` and rejects those fields, so the envelope is
    decoded and re-encoded through the shared codec first.
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
