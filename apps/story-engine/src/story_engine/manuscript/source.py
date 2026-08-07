from collections import Counter
from pathlib import Path

from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.projection import EventVisibility, ResolvedEvent
from story_engine.domain.simulation import BranchManifest, TurnSessionSnapshot
from story_engine.manuscript.models import (
    ManualSourceSelection,
    ManuscriptContext,
    ManuscriptContextManifest,
    ManuscriptContinuityContext,
    ManuscriptFactContext,
    ManuscriptSourceCandidate,
    ManuscriptSourceManifest,
    ManuscriptWritingIntent,
    ProjectCreativeContext,
    SceneSourceSelection,
    SourceSelection,
    SourceSelectionResult,
)
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.simulation_log import (
    SimulationLogRecord,
    SimulationLogStore,
)
from story_engine.wiki.context import WikiContextBuilder
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.scene_store import SceneDraftStore, SceneStore

MAX_CANDIDATES = 32
MAX_SOURCE_EVENTS = 128
MAX_SOURCE_CHARS = 131_072
MAX_SOURCE_MEMORY_RECORDS = 128
MAX_CONTINUITY_CHARS = 2_000


class SourceSelectionNotReadyError(ValueError):
    """Raised when the writer cannot choose a safe source range yet."""


class CandidateSourceBuilder:
    """Build deterministic, branch-local candidate ranges from committed turns."""

    def __init__(self, root: Path, branch_id: str) -> None:
        self.root = root
        self.branch_id = branch_id
        self.branches = BranchStore(root)
        self.checkpoints = CheckpointStore(root)
        self.logs = SimulationLogStore(root)

    def _branch(self) -> BranchManifest:
        return self.branches.load(self.branch_id)

    def _head(self, branch: BranchManifest) -> tuple[str, TurnSessionSnapshot]:
        checkpoint_id = branch.head_checkpoint_id
        if checkpoint_id is None:
            raise ValueError("branch has no checkpoint")
        snapshot = self.checkpoints.load(checkpoint_id)
        if snapshot.project_id != branch.project_id:
            raise ValueError("checkpoint belongs to another project")
        return checkpoint_id, snapshot

    def _records(
        self,
        branch: BranchManifest,
        checkpoint_id: str,
    ) -> tuple[SimulationLogRecord, ...]:
        return tuple(
            record
            for record in self.logs.reachable(self.checkpoints, checkpoint_id)
            if record.result.branch_id == branch.branch_id
            and record.result.resolved_turn is not None
        )

    def _branch_ids(self, branch: BranchManifest) -> tuple[str, ...]:
        result: list[str] = []
        seen: set[str] = set()
        current = branch
        while current.branch_id not in seen:
            seen.add(current.branch_id)
            result.append(current.branch_id)
            if current.parent_branch_id is None:
                return tuple(result)
            current = self.branches.load(current.parent_branch_id)
        raise ValueError("branch ancestry contains a cycle")

    def _wiki_version(self, branch: BranchManifest, checkpoint_id: str) -> str:
        for candidate in reversed(self.checkpoints.lineage(checkpoint_id)):
            for branch_id in self._branch_ids(branch):
                if WikiContextBuilder(self.root, branch_id).store.version_exists(
                    candidate
                ):
                    return candidate
        for branch_id in self._branch_ids(branch):
            if WikiContextBuilder(self.root, branch_id).store.version_exists("seed"):
                return "seed"
        raise ValueError("selected checkpoint has no reachable Wiki version")

    @staticmethod
    def _events(records: tuple[SimulationLogRecord, ...]) -> tuple[ResolvedEvent, ...]:
        return tuple(
            event
            for record in records
            if record.result.resolved_turn is not None
            for event in record.result.resolved_turn.events
        )

    @staticmethod
    def _ranges(
        records: tuple[SimulationLogRecord, ...],
    ) -> tuple[tuple[SimulationLogRecord, ...], ...]:
        groups: list[tuple[SimulationLogRecord, ...]] = []
        current: list[SimulationLogRecord] = []
        previous_step: int | None = None
        for record in records:
            step = record.result.step
            if current and previous_step is not None and step != previous_step + 1:
                groups.append(tuple(current))
                current = []
            current.append(record)
            previous_step = step
            if record.result.boundary.value != "none":
                groups.append(tuple(current))
                current = []
                previous_step = None
        if current:
            groups.append(tuple(current))
        return tuple(group for group in groups if CandidateSourceBuilder._events(group))

    def _covered_event_ids(self) -> set[str]:
        covered: set[str] = set()
        scenes = SceneStore(self.root, self.branch_id).list_scenes()
        drafts = SceneDraftStore(self.root, self.branch_id).list_drafts()
        for scene in scenes:
            covered.update(scene.source.event_ids)
        for draft in drafts:
            covered.update(draft.source.event_ids)
        return covered

    def _candidate(
        self,
        branch: BranchManifest,
        head_checkpoint_id: str,
        records: tuple[SimulationLogRecord, ...],
        covered_event_ids: set[str],
    ) -> ManuscriptSourceCandidate:
        events = self._events(records)
        event_ids = tuple(event.event_id for event in events)
        if len(event_ids) > MAX_SOURCE_EVENTS:
            raise ValueError("source candidate contains too many resolved events")
        event_summary_text = "\n".join(event.event_text for event in events)
        estimated_chars = len(event_summary_text)
        if not 0 < estimated_chars <= MAX_SOURCE_CHARS:
            raise ValueError("source candidate exceeds the character budget")
        checkpoint_id = records[-1].checkpoint_id or head_checkpoint_id
        from_step = records[0].result.step
        to_step = records[-1].result.step
        title_hint = events[0].event_text.splitlines()[0].strip()[:120]
        return ManuscriptSourceCandidate(
            source_id=f"source:{branch.branch_id}:{checkpoint_id}:{from_step}:{to_step}",
            branch_id=branch.branch_id,
            checkpoint_id=checkpoint_id,
            from_step=from_step,
            to_step=to_step,
            boundary=records[-1].result.boundary,
            title_hint=title_hint or f"Steps {from_step}-{to_step}",
            event_summary_text=event_summary_text,
            event_ids=event_ids,
            estimated_chars=estimated_chars,
            available_viewpoint_ids=tuple(
                self.checkpoints.load(checkpoint_id).roster_actor_ids
            ),
            wiki_version_id=self._wiki_version(branch, checkpoint_id),
            status=("covered" if set(event_ids) & covered_event_ids else "available"),
        )

    def all_candidates(self) -> tuple[ManuscriptSourceCandidate, ...]:
        branch = self._branch()
        head_checkpoint_id, _ = self._head(branch)
        records = self._records(branch, head_checkpoint_id)
        covered = self._covered_event_ids()
        candidates = tuple(
            self._candidate(branch, head_checkpoint_id, group, covered)
            for group in self._ranges(records)
        )
        return candidates

    def list_candidates(self) -> tuple[ManuscriptSourceCandidate, ...]:
        """Return only bounded ranges that have not already produced prose."""

        available = tuple(
            candidate
            for candidate in self.all_candidates()
            if candidate.status == "available"
        )
        selected: list[ManuscriptSourceCandidate] = []
        total_chars = 0
        for candidate in reversed(available):
            if len(selected) >= MAX_CANDIDATES:
                break
            if total_chars + candidate.estimated_chars > MAX_SOURCE_CHARS:
                break
            selected.append(candidate)
            total_chars += candidate.estimated_chars
        return tuple(reversed(selected))

    def _snapshot_for(self, checkpoint_id: str) -> TurnSessionSnapshot:
        branch = self._branch()
        head_checkpoint_id, _ = self._head(branch)
        if checkpoint_id not in self.checkpoints.lineage(head_checkpoint_id):
            raise ValueError("checkpoint is not part of the selected branch history")
        return self.checkpoints.load(checkpoint_id)

    @staticmethod
    def _memory_visible(record: MemoryRecord, viewpoint_actor_id: str | None) -> bool:
        if record.scope == MemoryScope.GAME_MASTER and "secret" in record.tags:
            return False
        if record.visible_to and (
            viewpoint_actor_id is None or viewpoint_actor_id not in record.visible_to
        ):
            return False
        if record.scope == MemoryScope.CHARACTER:
            return (
                viewpoint_actor_id is not None and record.owner_id == viewpoint_actor_id
            )
        return True

    def _memory_ids(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        from_step: int,
        to_step: int,
        viewpoint_actor_id: str | None,
        referenced_memory_ids: frozenset[str] = frozenset(),
    ) -> tuple[str, ...]:
        records: list[MemoryRecord] = []
        for memory_snapshot in snapshot.memory_snapshots.values():
            bank = ConcordiaMemoryBank(
                owner_id=memory_snapshot.owner_id,
                scope=memory_snapshot.scope,
            )
            bank.restore(memory_snapshot)
            records.extend(bank.scan(lambda _record: True))
        selected = [
            record
            for record in records
            if record.record_type
            not in {
                MemoryRecordType.PLAN,
                MemoryRecordType.PUTATIVE_EVENT,
                MemoryRecordType.SYSTEM,
            }
            and (
                from_step <= record.step <= to_step
                or record.record_id in referenced_memory_ids
            )
            and self._memory_visible(record, viewpoint_actor_id)
        ]
        selected.sort(key=lambda item: (item.step, item.created_at, item.record_id))
        return tuple(item.record_id for item in selected[:MAX_SOURCE_MEMORY_RECORDS])

    def _validate_candidate_sequence(
        self,
        candidates: tuple[ManuscriptSourceCandidate, ...],
        *,
        allow_covered: bool,
    ) -> tuple[tuple[str, ...], ManuscriptSourceCandidate, ManuscriptSourceCandidate]:
        if not candidates:
            raise ValueError("at least one manuscript source is required")
        branch = self._branch()
        head_checkpoint_id, _ = self._head(branch)
        lineage = self.checkpoints.lineage(head_checkpoint_id)
        reachable_checkpoints = set(lineage)
        checkpoint_positions = {
            checkpoint_id: index for index, checkpoint_id in enumerate(lineage)
        }
        previous_end: int | None = None
        previous_checkpoint_position: int | None = None
        event_ids: list[str] = []
        total_chars = 0
        for candidate in candidates:
            if candidate.branch_id != branch.branch_id:
                raise ValueError("manuscript source cannot cross branches")
            if candidate.status == "covered" and allow_covered:
                pass
            elif candidate.status != "available":
                raise ValueError("manuscript source is already covered")
            if candidate.checkpoint_id not in reachable_checkpoints:
                raise ValueError("manuscript source checkpoint is not reachable")
            checkpoint_position = checkpoint_positions[candidate.checkpoint_id]
            if (
                previous_checkpoint_position is not None
                and checkpoint_position <= previous_checkpoint_position
            ):
                raise ValueError("manuscript source checkpoint lineage moves backward")
            if previous_end is not None and candidate.from_step != previous_end + 1:
                raise ValueError("manuscript sources must be contiguous")
            previous_end = candidate.to_step
            previous_checkpoint_position = checkpoint_position
            event_ids.extend(candidate.event_ids)
            total_chars += candidate.estimated_chars
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("manuscript sources overlap resolved events")
        if len(event_ids) > MAX_SOURCE_EVENTS:
            raise ValueError("manuscript source contains too many resolved events")
        if total_chars > MAX_SOURCE_CHARS:
            raise ValueError("manuscript source exceeds the character budget")
        return tuple(event_ids), candidates[0], candidates[-1]

    def validate_manifest(self, source: ManuscriptSourceManifest) -> None:
        """Verify persisted source IDs still describe the current branch history."""
        branch = self._branch()
        if source.project_id != branch.project_id or (
            source.branch_id != branch.branch_id
        ):
            raise ValueError("manuscript source belongs to another branch")
        candidates_by_id = {
            candidate.source_id: candidate for candidate in self.all_candidates()
        }
        if len(source.source_ids) != len(set(source.source_ids)):
            raise ValueError("manuscript source ids must be unique")
        try:
            candidates = tuple(
                candidates_by_id[source_id] for source_id in source.source_ids
            )
        except KeyError as error:
            raise ValueError("manuscript source lineage was deleted") from error
        event_ids, first, last = self._validate_candidate_sequence(
            candidates,
            allow_covered=True,
        )
        if source.checkpoint_id != last.checkpoint_id:
            raise ValueError("manuscript source checkpoint changed")
        if source.from_step != first.from_step or source.to_step != last.to_step:
            raise ValueError("manuscript source step range changed")
        if source.event_ids != event_ids:
            raise ValueError("manuscript source events changed")
        if source.wiki_version_id != last.wiki_version_id:
            raise ValueError("manuscript source Wiki version changed")
        if source.viewpoint_actor_id is not None and (
            source.viewpoint_actor_id not in last.available_viewpoint_ids
        ):
            raise ValueError("viewpoint actor is unavailable at this checkpoint")

    def build_manifest(
        self,
        candidates: tuple[ManuscriptSourceCandidate, ...],
        *,
        viewpoint_actor_id: str | None,
    ) -> ManuscriptSourceManifest:
        branch = self._branch()
        event_ids, first, last = self._validate_candidate_sequence(
            candidates,
            allow_covered=False,
        )
        snapshot = self._snapshot_for(last.checkpoint_id)
        selected_records = tuple(
            record
            for record in self._records(branch, last.checkpoint_id)
            if first.from_step <= record.result.step <= last.to_step
        )
        resolved_viewpoint_actor_id = viewpoint_actor_id
        if resolved_viewpoint_actor_id is None:
            acting_actor_counts = Counter(
                record.result.resolved_turn.acting_actor_id
                for record in selected_records
                if record.result.resolved_turn is not None
                and record.result.resolved_turn.acting_actor_id is not None
            )
            resolved_viewpoint_actor_id = (
                acting_actor_counts.most_common(1)[0][0]
                if acting_actor_counts
                else None
            )
        selected_event_ids = set(event_ids)
        referenced_memory_ids = frozenset(
            memory_id
            for event in self._events(self._records(branch, last.checkpoint_id))
            if event.event_id in selected_event_ids
            for memory_id in event.source_memory_ids
        )
        if resolved_viewpoint_actor_id is not None and (
            resolved_viewpoint_actor_id not in snapshot.roster_actor_ids
            or resolved_viewpoint_actor_id not in snapshot.memory_snapshots
        ):
            raise ValueError("viewpoint actor is unavailable at this checkpoint")
        return ManuscriptSourceManifest(
            project_id=branch.project_id,
            branch_id=branch.branch_id,
            checkpoint_id=last.checkpoint_id,
            source_ids=tuple(candidate.source_id for candidate in candidates),
            from_step=first.from_step,
            to_step=last.to_step,
            event_ids=tuple(event_ids),
            memory_ids=self._memory_ids(
                snapshot,
                from_step=first.from_step,
                to_step=last.to_step,
                viewpoint_actor_id=resolved_viewpoint_actor_id,
                referenced_memory_ids=referenced_memory_ids,
            ),
            wiki_version_id=last.wiki_version_id,
            viewpoint_actor_id=resolved_viewpoint_actor_id,
        )


class SourceSelectionValidator:
    """Resolve a public selection into one immutable source manifest."""

    def __init__(self, builder: CandidateSourceBuilder) -> None:
        self.builder = builder

    def _lookup(
        self,
        source_ids: tuple[str, ...],
        candidates: tuple[ManuscriptSourceCandidate, ...],
    ) -> tuple[ManuscriptSourceCandidate, ...]:
        by_id = {candidate.source_id: candidate for candidate in candidates}
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("manuscript source selection contains duplicates")
        try:
            selected = tuple(by_id[source_id] for source_id in source_ids)
        except KeyError as error:
            raise ValueError("manuscript source is unavailable") from error
        if any(candidate.status != "available" for candidate in selected):
            raise ValueError("manuscript source is already covered")
        return selected

    def resolve(
        self,
        selection: SourceSelection,
        *,
        viewpoint_actor_id: str | None,
        writer_result: SourceSelectionResult | None = None,
    ) -> tuple[ManuscriptSourceManifest, str]:
        candidates = self.builder.all_candidates()
        if isinstance(selection, ManualSourceSelection):
            source_ids = selection.source_ids
            reason = "manual source selection"
        elif isinstance(selection, SceneSourceSelection):
            source_ids = (selection.source_id,)
            reason = "complete scene source selection"
        else:
            if writer_result is None:
                raise ValueError("writer source selection requires a writer decision")
            if writer_result.decision == "not_ready":
                raise SourceSelectionNotReadyError(writer_result.reason)
            source_ids = writer_result.source_ids
            reason = writer_result.reason
        selected = self._lookup(source_ids, candidates)
        return self.builder.build_manifest(
            selected,
            viewpoint_actor_id=viewpoint_actor_id,
        ), reason


class ManuscriptContextBuilder:
    """Build facts and continuity only after source selection has been frozen."""

    def __init__(self, root: Path, branch_id: str) -> None:
        self.root = root
        self.branch_id = branch_id
        self.sources = CandidateSourceBuilder(root, branch_id)
        self.branches = BranchStore(root)
        self.checkpoints = CheckpointStore(root)
        self.logs = SimulationLogStore(root)
        self.projects = ProjectStore(root)
        self.scenes = SceneStore(root, branch_id)

    def _branch(self) -> BranchManifest:
        return self.branches.load(self.branch_id)

    def _branch_ids(self, branch: BranchManifest) -> tuple[str, ...]:
        return self.sources._branch_ids(branch)

    def _wiki_branch(self, branch: BranchManifest, version_id: str) -> str:
        for branch_id in self._branch_ids(branch):
            if WikiContextBuilder(
                self.root,
                branch_id,
            ).store.version_exists(version_id):
                return branch_id
        raise ValueError("manuscript Wiki version is unavailable")

    @staticmethod
    def _events(records: tuple[SimulationLogRecord, ...]) -> tuple[ResolvedEvent, ...]:
        return tuple(
            event
            for record in records
            if record.result.resolved_turn is not None
            for event in record.result.resolved_turn.events
        )

    @staticmethod
    def _events_for_viewpoint(
        events: tuple[ResolvedEvent, ...], viewpoint_actor_id: str | None
    ) -> tuple[ResolvedEvent, ...]:
        if viewpoint_actor_id is None:
            return tuple(
                event for event in events if event.visibility == EventVisibility.PUBLIC
            )
        return tuple(
            event
            for event in events
            if event.visibility == EventVisibility.PUBLIC
            or (
                event.visibility == EventVisibility.PARTICIPANTS
                and viewpoint_actor_id in event.participant_ids
            )
            or (
                event.visibility == EventVisibility.RESTRICTED
                and viewpoint_actor_id in event.observer_ids
            )
        )

    @staticmethod
    def _records_to_snapshot(
        records: tuple[SimulationLogRecord, ...],
        source: ManuscriptSourceManifest,
    ) -> tuple[SimulationLogRecord, ...]:
        selected = tuple(
            record
            for record in records
            if source.from_step <= record.result.step <= source.to_step
        )
        events = ManuscriptContextBuilder._events(selected)
        if tuple(event.event_id for event in events) != source.event_ids:
            raise ValueError("manuscript source events changed")
        return selected

    @staticmethod
    def _decode_snapshot_memories(
        snapshot: TurnSessionSnapshot,
    ) -> tuple[MemoryRecord, ...]:
        records: list[MemoryRecord] = []
        for memory_snapshot in snapshot.memory_snapshots.values():
            bank = ConcordiaMemoryBank(
                owner_id=memory_snapshot.owner_id,
                scope=memory_snapshot.scope,
            )
            bank.restore(memory_snapshot)
            records.extend(bank.scan(lambda _record: True))
        return tuple(records)

    def _facts_memories(
        self,
        snapshot: TurnSessionSnapshot,
        source: ManuscriptSourceManifest,
    ) -> tuple[MemoryRecord, ...]:
        records = [
            record
            for record in self._decode_snapshot_memories(snapshot)
            if record.record_type
            not in {
                MemoryRecordType.PLAN,
                MemoryRecordType.PUTATIVE_EVENT,
                MemoryRecordType.SYSTEM,
            }
            and self.sources._memory_visible(record, source.viewpoint_actor_id)
            and (
                source.from_step <= record.step <= source.to_step
                or record.record_id in source.memory_ids
            )
        ]
        records.sort(key=lambda item: (item.step, item.created_at, item.record_id))
        selected = tuple(records[:MAX_SOURCE_MEMORY_RECORDS])
        if tuple(record.record_id for record in selected) != source.memory_ids:
            raise ValueError("manuscript source memories changed")
        return selected

    def _continuity(
        self,
        *,
        chapter_id: str,
    ) -> ManuscriptContinuityContext:
        scenes = self.scenes.list_scenes()
        same_chapter = [scene for scene in scenes if scene.chapter_id == chapter_id]
        previous = (
            (same_chapter or list(scenes))[-1] if (same_chapter or scenes) else None
        )
        if previous is None:
            return ManuscriptContinuityContext()
        return ManuscriptContinuityContext(
            previous_scene_id=previous.id,
            previous_scene_title=previous.title,
            previous_scene_excerpt=previous.body[-MAX_CONTINUITY_CHARS:],
        )

    def build_context(
        self,
        source: ManuscriptSourceManifest,
        *,
        intent: ManuscriptWritingIntent,
        selection_reason: str,
    ) -> tuple[ManuscriptContext, ManuscriptContextManifest]:
        self.sources.validate_manifest(source)
        branch = self._branch()
        if branch.project_id != source.project_id or source.branch_id != self.branch_id:
            raise ValueError("manuscript source belongs to another branch")
        snapshot = self.checkpoints.load(source.checkpoint_id)
        records = tuple(
            record
            for record in self.logs.reachable(self.checkpoints, source.checkpoint_id)
            if record.result.branch_id == self.branch_id
            and record.result.resolved_turn is not None
        )
        selected = self._records_to_snapshot(records, source)
        all_events = self._events(selected)
        events = self._events_for_viewpoint(all_events, source.viewpoint_actor_id)
        if not events:
            raise ValueError("manuscript context contains no visible resolved events")
        memories = self._facts_memories(snapshot, source)
        participants = tuple(
            sorted(
                {
                    actor_id
                    for event in events
                    for actor_id in (
                        *event.participant_ids,
                        *event.observer_ids,
                        *((event.actor_id,) if event.actor_id else ()),
                    )
                }
            )
        )
        locations = tuple(
            sorted({item for event in events for item in event.location_ids})
        )
        wiki_branch_id = self._wiki_branch(branch, source.wiki_version_id)
        wiki = WikiContextBuilder(
            self.root,
            wiki_branch_id,
            version_id=source.wiki_version_id,
            max_context_chars=32_768,
        ).writer(
            source.viewpoint_actor_id,
            participant_ids=participants,
            location_ids=locations,
            keywords=tuple(event.event_text for event in events),
        )
        project = self.projects.load().project
        facts = ManuscriptFactContext(
            events=events,
            memories=memories,
            wiki_context=wiki.content,
            wiki_manifest=wiki.manifest,
            project=ProjectCreativeContext(
                project_id=project.id,
                title=project.title,
                genre=project.genre,
                theme=project.theme,
                tone=project.tone,
                content_locale=snapshot.content_locale,
            ),
        )
        continuity = self._continuity(chapter_id=intent.chapter_id)
        context = ManuscriptContext(
            source=source,
            facts=facts,
            continuity=continuity,
            intent=intent,
        )
        manifest = ManuscriptContextManifest(
            wiki_page_paths=tuple(item.path for item in wiki.manifest),
            memory_ids=source.memory_ids,
            previous_scene_id=continuity.previous_scene_id,
            selected_source_ids=source.source_ids,
            selection_reason=selection_reason,
            target_words=intent.target_words,
            instruction=intent.instruction,
        )
        return context, manifest
