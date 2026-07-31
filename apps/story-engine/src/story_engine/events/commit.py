from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from pydantic import JsonValue

from story_engine.domain.errors import DomainError, InvalidTransitionError
from story_engine.domain.models import (
    Character,
    DomainModel,
    StateChange,
    StoryEvent,
    TurnCandidate,
    WorldState,
)
from story_engine.manuscript.models import (
    AmendmentCommitResult,
    EventAmendmentCandidate,
    Scene,
)
from story_engine.workspace.documents import (
    render_character,
    render_event,
    render_scene,
    render_world,
)
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.scene_store import SceneStore
from story_engine.workspace.transaction import AtomicBatch


class VersionConflictError(DomainError):
    """Raised when a candidate was based on stale canonical state."""


class StateChangeConflictError(DomainError):
    """Raised when a resolver change does not match current canonical state."""


class UnsupportedStateChangeError(DomainError):
    """Raised when a candidate tries to mutate a protected domain field."""


class CommitResult(DomainModel):
    event: StoryEvent
    candidate: TurnCandidate


class EventCommitService:
    _character_fields: ClassVar[frozenset[str]] = frozenset(
        {
            "current_goal",
            "location",
            "emotional_state",
            "resources",
            "known_fact_ids",
        }
    )
    _world_fields: ClassVar[frozenset[str]] = frozenset(
        {
            "current_time",
            "current_location",
            "active_pressures",
            "public_fact_ids",
            "world_variables",
        }
    )

    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = root
        self.project_store = ProjectStore(root)
        self.event_store = EventStore(root)
        self.clock = clock or (lambda: datetime.now(UTC))

    def commit(self, candidate: TurnCandidate) -> CommitResult:
        if candidate.status != "approved":
            raise InvalidTransitionError(
                "only an approved candidate can change formal state"
            )

        snapshot = self.project_store.load()
        if snapshot.project.id != candidate.project_id:
            raise VersionConflictError("candidate project does not match workspace")
        if snapshot.world.version != candidate.base_world_version:
            raise VersionConflictError(
                "world version changed since candidate generation"
            )

        characters = {character.id: character for character in snapshot.characters}
        for character_id, base_version in candidate.base_character_versions.items():
            current = characters.get(character_id)
            if current is None:
                raise VersionConflictError(
                    f"character no longer exists: {character_id}"
                )
            if current.version != base_version:
                raise VersionConflictError(
                    f"character version changed: {character_id}"
                )
        for intent in candidate.intents:
            if intent.character_id not in candidate.base_character_versions:
                raise VersionConflictError(
                    f"missing base character version: {intent.character_id}"
                )

        sequence = self.event_store.next_sequence()
        event_id = f"event-{sequence:06d}"
        updated_world = self._apply_world_changes(
            snapshot.world,
            candidate.outcome.world_changes,
        )
        updated_characters = self._apply_character_changes(
            characters,
            candidate.outcome.character_changes,
            event_id,
            tuple(intent.character_id for intent in candidate.intents),
        )
        event = StoryEvent(
            id=event_id,
            sequence=sequence,
            occurred_at=self.clock(),
            summary=candidate.outcome.summary,
            participants=tuple(intent.character_id for intent in candidate.intents),
            public_results=candidate.outcome.public_results,
            hidden_results=candidate.outcome.hidden_results,
            character_changes=candidate.outcome.character_changes,
            world_changes=candidate.outcome.world_changes,
            source_turn_id=candidate.id,
            approved_by_user=True,
        )

        batch = AtomicBatch(self.root)
        batch.add("world.md", render_world(updated_world))
        for character_id in sorted(updated_characters):
            character = updated_characters[character_id]
            relative = self.project_store.character_path(character).relative_to(
                self.root
            )
            batch.add(relative.as_posix(), render_character(character))
        batch.add(
            f"events/{sequence:06d}.md",
            render_event(event),
            overwrite=False,
        )
        batch.commit()
        return CommitResult(event=event, candidate=candidate.mark_committed())

    def commit_scene(self, scene: Scene, *, expected_version: int) -> Scene:
        self._validate_scene(scene, expected_version=expected_version)
        batch = AtomicBatch(self.root)
        batch.add(
            SceneStore.relative_path(scene),
            render_scene(scene),
            overwrite=expected_version > 0,
        )
        batch.commit()
        return scene

    def commit_scene_amendment(
        self,
        scene: Scene,
        amendment: EventAmendmentCandidate,
    ) -> AmendmentCommitResult:
        if amendment.status != "pending":
            raise InvalidTransitionError("only a pending amendment can be committed")
        if amendment.project_id != scene.project_id or amendment.scene_id != scene.id:
            raise VersionConflictError("amendment does not match scene")
        snapshot = self.project_store.load()
        if snapshot.project.id != amendment.project_id:
            raise VersionConflictError("amendment project does not match workspace")
        if snapshot.world.version != amendment.base_world_version:
            raise VersionConflictError("world version changed since manuscript review")
        self._validate_scene(scene, expected_version=scene.version - 1)

        source_events = {
            event.id: event for event in self.event_store.list_events()
        }
        selected = [
            source_events.get(event_id) for event_id in amendment.source_event_ids
        ]
        if any(event is None or not event.approved_by_user for event in selected):
            raise VersionConflictError("amendment source event is no longer confirmed")

        previous_fact_ids = snapshot.world.public_fact_ids
        next_fact_ids = (*previous_fact_ids, *amendment.fact_ids)
        world_change = StateChange(
            target_type="world",
            target_id="world",
            field="public_fact_ids",
            old_value=list(previous_fact_ids),
            new_value=list(next_fact_ids),
            reason="用户确认正文新增事实的 Event Amendment",
        )
        updated_world = snapshot.world.model_copy(
            update={
                "public_fact_ids": next_fact_ids,
                "version": snapshot.world.version + 1,
            }
        )
        sequence = self.event_store.next_sequence()
        event = StoryEvent(
            id=f"event-{sequence:06d}",
            sequence=sequence,
            occurred_at=self.clock(),
            summary="正文补充事实: " + "; ".join(amendment.proposed_facts),
            participants=tuple(
                sorted(
                    {
                        participant
                        for event in selected
                        if event is not None
                        for participant in event.participants
                    }
                )
            ),
            public_results=amendment.proposed_facts,
            world_changes=(world_change,),
            source_turn_id=amendment.id,
            approved_by_user=True,
        )
        committed = amendment.mark_committed()
        batch = AtomicBatch(self.root)
        batch.add("world.md", render_world(updated_world))
        batch.add(
            f"events/{sequence:06d}.md",
            render_event(event),
            overwrite=False,
        )
        batch.add(
            SceneStore.relative_path(scene),
            render_scene(scene),
            overwrite=scene.version > 1,
        )
        batch.commit()
        return AmendmentCommitResult(
            scene=scene,
            amendment=committed,
            event=event,
        )

    def _validate_scene(self, scene: Scene, *, expected_version: int) -> None:
        snapshot = self.project_store.load()
        if snapshot.project.id != scene.project_id:
            raise VersionConflictError("scene project does not match workspace")
        try:
            current = SceneStore(self.root).load(scene.id)
        except FileNotFoundError:
            current = None
        if expected_version == 0 and current is not None:
            raise VersionConflictError("scene already exists")
        if expected_version > 0 and (
            current is None or current.version != expected_version
        ):
            raise VersionConflictError("scene version changed")

    def _apply_world_changes(
        self,
        world: WorldState,
        changes: tuple[StateChange, ...],
    ) -> WorldState:
        relevant = [change for change in changes if change.target_type == "world"]
        if not relevant:
            return world.model_copy(update={"version": world.version + 1})

        data = world.model_dump()
        for change in relevant:
            if change.target_id != "world":
                raise StateChangeConflictError(
                    f"unknown world target: {change.target_id}"
                )
            if change.field.startswith("world_variables."):
                key = change.field.removeprefix("world_variables.")
                variables = dict(data["world_variables"])
                self._check_old_value(variables.get(key), change)
                variables[key] = change.new_value
                data["world_variables"] = variables
                continue
            if change.field not in self._world_fields:
                raise UnsupportedStateChangeError(
                    f"world field cannot be changed: {change.field}"
                )
            self._check_old_value(data.get(change.field), change)
            data[change.field] = change.new_value
        data["version"] = world.version + 1
        return WorldState.model_validate(data)

    def _apply_character_changes(
        self,
        characters: dict[str, Character],
        changes: tuple[StateChange, ...],
        event_id: str,
        participant_ids: tuple[str, ...],
    ) -> dict[str, Character]:
        grouped: dict[str, list[StateChange]] = {}
        for change in changes:
            if change.target_type == "character":
                grouped.setdefault(change.target_id, []).append(change)

        updated = dict(characters)
        participants = set(grouped) | set(participant_ids)
        for character_id in participants:
            current = characters.get(character_id)
            if current is None:
                raise StateChangeConflictError(
                    f"unknown character target: {character_id}"
                )
            data = current.model_dump()
            for change in grouped.get(character_id, ()):
                if change.field not in self._character_fields:
                    raise UnsupportedStateChangeError(
                        f"character field cannot be changed: {change.field}"
                    )
                self._check_old_value(data.get(change.field), change)
                data[change.field] = change.new_value
            data["last_event_id"] = event_id
            data["version"] = current.version + 1
            updated[character_id] = Character.model_validate(data)

        return updated

    @staticmethod
    def _check_old_value(current: object, change: StateChange) -> None:
        expected: JsonValue = change.old_value
        if expected is not None and current != expected:
            raise StateChangeConflictError(
                f"state changed before commit: {change.target_id}.{change.field}"
            )
