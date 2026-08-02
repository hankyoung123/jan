from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from pydantic import JsonValue

from story_engine.domain.errors import DomainError, InvalidTransitionError
from story_engine.domain.models import (
    Character,
    DomainModel,
    Fact,
    FactCandidate,
    KnowledgeChange,
    NpcCandidate,
    PromotionCandidate,
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
    render_fact,
    render_scene,
    render_world,
)
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.fact_store import FactStore
from story_engine.workspace.lock import ProjectLock
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.scene_store import SceneStore
from story_engine.workspace.session import canonical_revision
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


class PromotionCommitResult(DomainModel):
    event: StoryEvent
    character: Character
    candidate: PromotionCandidate


class EventCommitService:
    _character_fields: ClassVar[frozenset[str]] = frozenset(
        {
            "current_goal",
            "location",
            "emotional_state",
            "resources",
        }
    )
    _world_fields: ClassVar[frozenset[str]] = frozenset(
        {
            "current_time",
            "current_location",
            "active_pressures",
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
        with ProjectLock(self.root):
            return self._commit_locked(candidate)

    def _commit_locked(self, candidate: TurnCandidate) -> CommitResult:
        if candidate.status != "approved":
            raise InvalidTransitionError(
                "only an approved candidate can change formal state"
            )

        self._assert_workspace_revision(candidate.base_workspace_revision)
        snapshot = self.project_store.load()
        if snapshot.project.id != candidate.project_id:
            raise VersionConflictError("candidate project does not match workspace")
        if snapshot.world.version != candidate.base_world_version:
            raise VersionConflictError(
                "world version changed since candidate generation"
            )

        characters = {character.id: character for character in snapshot.characters}
        required_character_versions = {
            intent.character_id for intent in candidate.intents
        }
        required_character_versions.update(
            change.target_id
            for change in candidate.outcome.character_changes
            if change.target_type == "character"
        )
        required_character_versions.update(
            change.character_id for change in candidate.outcome.knowledge_changes
        )
        required_character_versions.update(
            character_id
            for fact in candidate.outcome.fact_candidates
            for character_id in fact.known_by
            if character_id in characters
        )
        for character_id in sorted(required_character_versions):
            if character_id not in candidate.base_character_versions:
                raise VersionConflictError(
                    f"missing base character version: {character_id}"
                )
        for character_id, base_version in candidate.base_character_versions.items():
            current = characters.get(character_id)
            if current is None:
                raise VersionConflictError(
                    f"character no longer exists: {character_id}"
                )
            if current.version != base_version:
                raise VersionConflictError(f"character version changed: {character_id}")

        sequence = self.event_store.next_sequence()
        event_id = f"event-{sequence:06d}"
        occurred_at = self.clock()
        facts = self._materialize_facts(
            candidate.outcome.fact_candidates,
            existing_facts=snapshot.facts,
            character_ids=set(characters)
            | {npc.id for npc in candidate.outcome.new_npcs},
            event_id=event_id,
            occurred_at=occurred_at,
        )
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
        created_npcs = self._create_npcs(
            candidate.outcome.new_npcs,
            existing_characters=characters,
            event_id=event_id,
            location=updated_world.current_location,
        )
        public_fact_ids = tuple(
            fact.id for fact in facts if fact.visibility == "public"
        )
        updated_world = updated_world.model_copy(
            update={
                "public_fact_ids": (
                    *updated_world.public_fact_ids,
                    *public_fact_ids,
                )
            }
        )
        updated_characters, created_npcs = self._apply_knowledge_changes(
            existing=characters,
            updated=updated_characters,
            created=created_npcs,
            facts=facts,
            changes=candidate.outcome.knowledge_changes,
            all_facts=(*snapshot.facts, *facts),
            event_id=event_id,
        )
        event = StoryEvent(
            id=event_id,
            sequence=sequence,
            occurred_at=occurred_at,
            summary=candidate.outcome.summary,
            participants=tuple(intent.character_id for intent in candidate.intents),
            fact_ids=tuple(fact.id for fact in facts),
            knowledge_changes=candidate.outcome.knowledge_changes,
            character_changes=candidate.outcome.character_changes,
            world_changes=candidate.outcome.world_changes,
            source_turn_id=candidate.id,
            approved_by_user=True,
        )
        formal_facts = (*snapshot.facts, *facts)

        batch = AtomicBatch(self.root)
        batch.add("world.md", render_world(updated_world, formal_facts))
        for character_id in sorted(updated_characters):
            character = updated_characters[character_id]
            relative = self.project_store.character_path(character).relative_to(
                self.root
            )
            batch.add(
                relative.as_posix(),
                render_character(character, formal_facts),
            )
        for character_id in sorted(created_npcs):
            character = created_npcs[character_id]
            relative = self.project_store.character_path(character).relative_to(
                self.root
            )
            batch.add(
                relative.as_posix(),
                render_character(character, formal_facts),
                overwrite=False,
            )
        for fact in facts:
            batch.add(
                FactStore.relative_path(fact.id),
                render_fact(fact),
                overwrite=False,
            )
        batch.add(
            f"events/{sequence:06d}.md",
            render_event(event, facts),
            overwrite=False,
        )
        self._assert_workspace_revision(candidate.base_workspace_revision)
        batch.commit()
        return CommitResult(event=event, candidate=candidate.mark_committed())

    def promote_npc(self, candidate: PromotionCandidate) -> PromotionCommitResult:
        with ProjectLock(self.root):
            return self._promote_npc_locked(candidate)

    def _promote_npc_locked(
        self,
        candidate: PromotionCandidate,
    ) -> PromotionCommitResult:
        if candidate.status != "pending":
            raise InvalidTransitionError("only a pending promotion can be committed")

        self._assert_workspace_revision(candidate.base_workspace_revision)
        snapshot = self.project_store.load()
        if snapshot.project.id != candidate.project_id:
            raise VersionConflictError("promotion project does not match workspace")
        characters = {character.id: character for character in snapshot.characters}
        current = characters.get(candidate.character_id)
        if current is None:
            raise VersionConflictError("promotion character no longer exists")
        if current.type != "npc":
            raise InvalidTransitionError("only an NPC can be promoted")
        if current.version != candidate.base_character_version:
            raise VersionConflictError(
                "character version changed since promotion review"
            )

        sequence = self.event_store.next_sequence()
        event_id = f"event-{sequence:06d}"
        promoted = current.model_copy(
            update={
                "type": "active",
                "current_goal": candidate.proposed_goal,
                "last_event_id": event_id,
                "version": current.version + 1,
            }
        )
        promoted = Character.model_validate(promoted)
        changes = [
            StateChange(
                target_type="character",
                target_id=current.id,
                field="type",
                old_value="npc",
                new_value="active",
                reason="用户确认 Editor 的角色升级建议",
            )
        ]
        if current.current_goal != promoted.current_goal:
            changes.append(
                StateChange(
                    target_type="character",
                    target_id=current.id,
                    field="current_goal",
                    old_value=current.current_goal,
                    new_value=promoted.current_goal,
                    reason="采用已确认升级建议中的独立目标",
                )
            )
        display_name = promoted.display_name or promoted.id
        event = StoryEvent(
            id=event_id,
            sequence=sequence,
            occurred_at=self.clock(),
            summary=f"{display_name} 升级为活跃角色。",
            participants=(promoted.id,),
            character_changes=tuple(changes),
            source_turn_id=candidate.id,
            approved_by_user=True,
        )

        previous_path = self.project_store.character_path(current).relative_to(
            self.root
        )
        promoted_path = self.project_store.character_path(promoted).relative_to(
            self.root
        )
        batch = AtomicBatch(self.root)
        batch.add(
            promoted_path.as_posix(),
            render_character(promoted, snapshot.facts),
            overwrite=False,
        )
        batch.delete(previous_path.as_posix())
        batch.add(
            f"events/{sequence:06d}.md",
            render_event(event),
            overwrite=False,
        )
        self._assert_workspace_revision(candidate.base_workspace_revision)
        batch.commit()
        return PromotionCommitResult(
            event=event,
            character=promoted,
            candidate=candidate.mark_committed(),
        )

    @staticmethod
    def _create_npcs(
        candidates: tuple[NpcCandidate, ...],
        *,
        existing_characters: dict[str, Character],
        event_id: str,
        location: str | None,
    ) -> dict[str, Character]:
        created: dict[str, Character] = {}
        for candidate in candidates:
            if candidate.id in existing_characters:
                raise StateChangeConflictError(f"NPC ID already exists: {candidate.id}")
            created[candidate.id] = Character(
                id=candidate.id,
                type="npc",
                identity=candidate.identity,
                core_desire=candidate.purpose,
                current_goal=candidate.current_goal,
                location=location,
                last_event_id=event_id,
            )
        return created

    @staticmethod
    def _materialize_facts(
        candidates: tuple[FactCandidate, ...],
        *,
        existing_facts: tuple[Fact, ...],
        character_ids: set[str],
        event_id: str,
        occurred_at: datetime,
    ) -> tuple[Fact, ...]:
        existing_ids = {fact.id for fact in existing_facts}
        candidate_ids = {candidate.id for candidate in candidates}
        if existing_ids.intersection(candidate_ids):
            raise StateChangeConflictError("fact ID already exists")
        available_fact_ids = existing_ids | candidate_ids
        facts: list[Fact] = []
        for candidate in candidates:
            unknown_characters = set(candidate.known_by) - character_ids
            if unknown_characters:
                raise StateChangeConflictError(
                    "fact references unknown characters: "
                    + ", ".join(sorted(unknown_characters))
                )
            if (
                candidate.supersedes_fact_id is not None
                and candidate.supersedes_fact_id not in available_fact_ids
            ):
                raise StateChangeConflictError(
                    f"fact supersedes unknown fact: {candidate.supersedes_fact_id}"
                )
            facts.append(
                Fact(
                    **candidate.model_dump(),
                    source_event_id=event_id,
                    introduced_at=occurred_at,
                )
            )
        return tuple(facts)

    @staticmethod
    def _apply_knowledge_changes(
        *,
        existing: dict[str, Character],
        updated: dict[str, Character],
        created: dict[str, Character],
        facts: tuple[Fact, ...],
        changes: tuple[KnowledgeChange, ...],
        all_facts: tuple[Fact, ...],
        event_id: str,
    ) -> tuple[dict[str, Character], dict[str, Character]]:
        characters = {**updated, **created}
        known_fact_ids = {fact.id for fact in all_facts}
        actions = [
            KnowledgeChange(
                character_id=character_id,
                fact_id=fact.id,
                action="learn",
                reason="角色是新事实的初始知情者",
            )
            for fact in facts
            if fact.visibility != "public"
            for character_id in fact.known_by
        ]
        actions.extend(changes)

        touched: set[str] = set()
        for change in actions:
            character = characters.get(change.character_id)
            if character is None:
                raise StateChangeConflictError(
                    f"knowledge change targets unknown character: {change.character_id}"
                )
            if change.fact_id not in known_fact_ids:
                raise StateChangeConflictError(
                    f"knowledge change references unknown fact: {change.fact_id}"
                )
            fact_ids = list(character.known_fact_ids)
            if change.action == "learn" and change.fact_id not in fact_ids:
                fact_ids.append(change.fact_id)
            if change.action == "forget" and change.fact_id in fact_ids:
                fact_ids.remove(change.fact_id)
            data = character.model_dump()
            data["known_fact_ids"] = tuple(fact_ids)
            if change.character_id in existing and change.character_id not in touched:
                if character.version == existing[change.character_id].version:
                    data["version"] = character.version + 1
                data["last_event_id"] = event_id
            characters[change.character_id] = Character.model_validate(data)
            touched.add(change.character_id)

        return (
            {character_id: characters[character_id] for character_id in updated},
            {character_id: characters[character_id] for character_id in created},
        )

    def commit_scene(
        self,
        scene: Scene,
        *,
        expected_version: int,
        expected_workspace_revision: str | None,
    ) -> Scene:
        with ProjectLock(self.root):
            return self._commit_scene_locked(
                scene,
                expected_version=expected_version,
                expected_workspace_revision=expected_workspace_revision,
            )

    def _commit_scene_locked(
        self,
        scene: Scene,
        *,
        expected_version: int,
        expected_workspace_revision: str | None,
    ) -> Scene:
        self._validate_scene(
            scene,
            expected_version=expected_version,
            expected_workspace_revision=expected_workspace_revision,
        )
        batch = AtomicBatch(self.root)
        batch.add(
            SceneStore.relative_path(scene),
            render_scene(scene),
            overwrite=expected_version > 0,
        )
        self._assert_workspace_revision(expected_workspace_revision)
        batch.commit()
        return scene

    def commit_scene_amendment(
        self,
        scene: Scene,
        amendment: EventAmendmentCandidate,
    ) -> AmendmentCommitResult:
        with ProjectLock(self.root):
            return self._commit_scene_amendment_locked(scene, amendment)

    def _commit_scene_amendment_locked(
        self,
        scene: Scene,
        amendment: EventAmendmentCandidate,
    ) -> AmendmentCommitResult:
        if amendment.status != "pending":
            raise InvalidTransitionError("only a pending amendment can be committed")
        if amendment.project_id != scene.project_id or amendment.scene_id != scene.id:
            raise VersionConflictError("amendment does not match scene")
        self._assert_workspace_revision(amendment.base_workspace_revision)
        snapshot = self.project_store.load()
        if snapshot.project.id != amendment.project_id:
            raise VersionConflictError("amendment project does not match workspace")
        if snapshot.world.version != amendment.base_world_version:
            raise VersionConflictError("world version changed since manuscript review")
        self._validate_scene(
            scene,
            expected_version=scene.version - 1,
            expected_workspace_revision=amendment.base_workspace_revision,
        )

        source_events = {event.id: event for event in self.event_store.list_events()}
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
        occurred_at = self.clock()
        facts = tuple(
            Fact(
                id=fact_id,
                statement=statement,
                visibility="public",
                source_event_id=f"event-{sequence:06d}",
                introduced_at=occurred_at,
            )
            for fact_id, statement in zip(
                amendment.fact_ids,
                amendment.proposed_facts,
                strict=True,
            )
        )
        existing_fact_ids = {fact.id for fact in snapshot.facts}
        if existing_fact_ids.intersection(amendment.fact_ids):
            raise StateChangeConflictError("amendment fact ID already exists")
        event = StoryEvent(
            id=f"event-{sequence:06d}",
            sequence=sequence,
            occurred_at=occurred_at,
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
            fact_ids=amendment.fact_ids,
            world_changes=(world_change,),
            source_turn_id=amendment.id,
            approved_by_user=True,
        )
        committed = amendment.mark_committed()
        batch = AtomicBatch(self.root)
        formal_facts = (*snapshot.facts, *facts)
        batch.add("world.md", render_world(updated_world, formal_facts))
        for fact in facts:
            batch.add(
                FactStore.relative_path(fact.id),
                render_fact(fact),
                overwrite=False,
            )
        batch.add(
            f"events/{sequence:06d}.md",
            render_event(event, facts),
            overwrite=False,
        )
        batch.add(
            SceneStore.relative_path(scene),
            render_scene(scene),
            overwrite=scene.version > 1,
        )
        self._assert_workspace_revision(amendment.base_workspace_revision)
        batch.commit()
        return AmendmentCommitResult(
            scene=scene,
            amendment=committed,
            event=event,
        )

    def _validate_scene(
        self,
        scene: Scene,
        *,
        expected_version: int,
        expected_workspace_revision: str | None,
    ) -> None:
        self._assert_workspace_revision(expected_workspace_revision)
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

    def _assert_workspace_revision(self, expected: str | None) -> None:
        if expected is None:
            raise VersionConflictError(
                "candidate is missing its canonical workspace revision"
            )
        try:
            current = canonical_revision(self.root)
        except (OSError, ValueError) as error:
            raise VersionConflictError(
                "canonical workspace is invalid or changed"
            ) from error
        if current != expected:
            raise VersionConflictError("canonical workspace changed since review")

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
