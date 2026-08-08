"""Create a canonical story project from a validated submission package."""

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from story_engine.domain.models import Character, Fact, WorldState
from story_engine.wiki.store import WikiStore
from story_engine.workspace.project_store import (
    ProjectSeed,
    ProjectSnapshot,
    ProjectStore,
)

if TYPE_CHECKING:
    from story_engine.submission.service import SubmissionPackage


class SubmissionService:
    """Materialize the one canonical project owned by a submission."""

    def __init__(self, projects_root: Path) -> None:
        self.projects_root = projects_root

    def finalize(self, package: "SubmissionPackage") -> ProjectSnapshot:
        from story_engine.submission.service import SubmissionNotRunnableError

        distinct_goals = {character.current_goal for character in package.characters}
        if not package.pressures and len(distinct_goals) < 2:
            raise SubmissionNotRunnableError(
                "submission requires world pressure or conflicting character goals"
            )
        seed = ProjectSeed(
            id=package.id,
            title=package.title,
            genre=package.genre,
            theme=package.theme,
            tone=package.tone,
            world=WorldState(
                current_time=package.initial_time,
                current_location=package.initial_location,
                scene_text=package.scene_text,
                rules=package.world_rules,
                active_pressures=package.pressures,
                public_fact_ids=tuple(
                    fact.id for fact in package.facts if fact.visibility == "public"
                ),
                world_variables={
                    "initial_incident": package.initial_incident,
                    "round": 0,
                },
            ),
            characters=tuple(
                Character(
                    id=item.id,
                    display_name=item.display_name,
                    type="active",
                    identity=item.identity,
                    core_desire=item.core_desire,
                    current_goal=item.current_goal,
                    known_fact_ids=item.known_fact_ids,
                    relationships=item.relationships,
                    capabilities=item.capabilities,
                    conditions=item.conditions,
                    location=item.location,
                    emotional_state=item.emotional_state,
                    resources=item.resources,
                )
                for item in package.characters
            ),
            facts=tuple(
                Fact(
                    **fact.model_dump(),
                    source_event_id=f"submission:{package.id}",
                    introduced_at=datetime.now(UTC),
                )
                for fact in package.facts
            ),
        )
        root = self.projects_root / package.id
        snapshot = ProjectStore(root).create(seed)
        WikiStore(root, "main").initialize(snapshot)
        return snapshot
