from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from story_engine.domain.errors import DomainError, InvalidTransitionError
from story_engine.domain.models import (
    Character,
    DomainModel,
    Fact,
    PromotionCandidate,
    StateChange,
    StoryEvent,
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
    """Raised when a manuscript change conflicts with canonical state."""


class PromotionCommitResult(DomainModel):
    event: StoryEvent
    character: Character
    candidate: PromotionCandidate


class EventCommitService:
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
            source_record_id=candidate.id,
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
            source_record_id=amendment.id,
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
