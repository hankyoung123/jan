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
from story_engine.workspace.documents import (
    render_character,
    render_event,
    render_world,
)
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectStore
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
