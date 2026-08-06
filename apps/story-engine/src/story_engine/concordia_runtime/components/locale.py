from concordia.components.agent import (  # type: ignore[import-untyped]
    action_spec_ignored,
)
from concordia.typing import entity_component  # type: ignore[import-untyped]


class LocalePolicy(action_spec_ignored.ActionSpecIgnored):  # type: ignore[misc]
    """Constrain natural language without translating machine fields."""

    def __init__(self, content_locale: str) -> None:
        super().__init__("Content language policy")
        self._content_locale = content_locale

    def _make_pre_act_value(self) -> str:
        return (
            f"Write all natural-language observations, actions, dialogue, and "
            f"events in {self._content_locale}. Preserve IDs, enum values, tags, "
            "references, paths, and hashes exactly; never translate them."
        )

    def get_state(self) -> entity_component.ComponentState:
        return {"content_locale": self._content_locale}

    def set_state(self, state: entity_component.ComponentState) -> None:
        if "content_locale" in state:
            self._content_locale = str(state["content_locale"])
