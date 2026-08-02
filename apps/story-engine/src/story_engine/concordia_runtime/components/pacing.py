from concordia.components.agent import (  # type: ignore[import-untyped]
    action_spec_ignored,
)
from concordia.typing import entity_component  # type: ignore[import-untyped]


class PacingContext(action_spec_ignored.ActionSpecIgnored):  # type: ignore[misc]
    """Provide a scene objective and pacing guidance without fixing outcomes."""

    def __init__(
        self,
        *,
        scene_goal: str,
        pacing: str = "Let consequences emerge before escalating conflict.",
    ) -> None:
        super().__init__("Scene and pacing")
        self._scene_goal = scene_goal
        self._pacing = pacing

    def _make_pre_act_value(self) -> str:
        return f"Scene objective: {self._scene_goal}\nPacing guidance: {self._pacing}"

    def get_state(self) -> entity_component.ComponentState:
        return {"scene_goal": self._scene_goal, "pacing": self._pacing}

    def set_state(self, state: entity_component.ComponentState) -> None:
        if "scene_goal" in state:
            self._scene_goal = str(state["scene_goal"])
        if "pacing" in state:
            self._pacing = str(state["pacing"])
