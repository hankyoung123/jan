from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import pytest

from story_engine.concordia_runtime.roster import ConcordiaRosterPlanner
from story_engine.domain.projection import EventVisibility, ResolvedEvent


class RosterModel:
    def __init__(self, actor_names: list[str]) -> None:
        self.actor_names = actor_names
        self.calls: list[tuple[str, Mapping[str, Any]]] = []

    def sample_json(
        self,
        prompt: str,
        schema: Mapping[str, Any],
        *,
        temperature: float = 0.1,
    ) -> Mapping[str, Any]:
        assert temperature == 0.1
        self.calls.append((prompt, schema))
        return {"actor_names": self.actor_names}


def _candidates(count: int = 6) -> dict[str, tuple[str, str]]:
    return {
        f"agent-{index}": (f"Active Agent {index}", f"Goal {index}")
        for index in range(count)
    }


def test_opening_scene_roster_schema_caps_selection_at_four() -> None:
    model = RosterModel(
        ["Active Agent 0", "Active Agent 1", "Active Agent 2", "Active Agent 3"]
    )
    planner = ConcordiaRosterPlanner(
        model=model,
        premise_text="The harbor loses power.",
        content_locale="en-US",
    )

    selected = planner.select_initial(_candidates())

    actor_names_schema = model.calls[0][1]["properties"]["actor_names"]
    assert actor_names_schema["maxItems"] == 4
    assert len(actor_names_schema["items"]["enum"]) == 6
    assert selected == ("agent-0", "agent-1", "agent-2", "agent-3")


def test_next_scene_can_select_a_newly_promoted_agent() -> None:
    model = RosterModel(["Investigator", "Independent witness"])
    planner = ConcordiaRosterPlanner(
        model=model,
        premise_text="The harbor loses power.",
        content_locale="en-US",
    )
    event = ResolvedEvent(
        event_id="event:session-1:1",
        session_id="session-1",
        step=1,
        event_text="The witness decides to pursue the saboteur.",
        visibility=EventVisibility.PARTICIPANTS,
        participant_ids=("promoted-agent",),
        content_locale="en-US",
        occurred_at=datetime(2026, 8, 4, tzinfo=UTC),
    )

    selected = planner.select_next(
        {
            "agent-0": ("Investigator", "Find the saboteur"),
            "promoted-agent": ("Independent witness", "Pursue the saboteur"),
        },
        current_roster=("agent-0",),
        scene_events=(event,),
    )

    assert selected == ("agent-0", "promoted-agent")
    assert "Select the roster for the next scene" in model.calls[0][0]
    assert "Independent witness" in model.calls[0][0]
    assert "promoted-agent" not in model.calls[0][0]


def test_roster_rejects_more_than_four_agents_even_if_model_breaks_schema() -> None:
    model = RosterModel([f"Active Agent {index}" for index in range(5)])
    planner = ConcordiaRosterPlanner(
        model=model,
        premise_text="The harbor loses power.",
        content_locale="en-US",
    )

    with pytest.raises(ValueError, match="too many"):
        planner.select_initial(_candidates())
