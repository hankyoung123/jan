from collections import Counter
from pathlib import Path

from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.narrative import (
    EditorContext,
    NarrativeSource,
    NarrativeSourceStatus,
    NarrativeSourceSummary,
    WriterContext,
)
from story_engine.domain.projection import (
    EventVisibility,
    ResolvedEvent,
    SimulationBoundary,
)
from story_engine.domain.simulation import BranchManifest, TurnSessionSnapshot
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.simulation_log import (
    SimulationLogRecord,
    SimulationLogStore,
)
from story_engine.wiki.context import WikiContextBuilder
from story_engine.workspace.documents import load_json_envelope


class NarrativeSourceReader:
    """Build branch-scoped Writer input directly from durable runtime history."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.branches = BranchStore(root)
        self.checkpoints = CheckpointStore(root)
        self.logs = SimulationLogStore(root)

    def _branch(self, branch_id: str) -> BranchManifest:
        branch = self.branches.load(branch_id)
        return branch

    def _select_checkpoint(
        self,
        branch: BranchManifest,
        checkpoint_id: str | None,
    ) -> tuple[str, TurnSessionSnapshot]:
        selected = checkpoint_id or branch.head_checkpoint_id
        if selected is None:
            raise ValueError("branch has no checkpoint")
        if branch.head_checkpoint_id is None:
            raise ValueError("branch has no checkpoint")
        reachable = set(self.checkpoints.lineage(branch.head_checkpoint_id))
        if selected not in reachable:
            raise ValueError("checkpoint is not part of the selected branch history")
        snapshot = self.checkpoints.load(selected)
        if snapshot.project_id != branch.project_id:
            raise ValueError("checkpoint belongs to another project")
        return selected, snapshot

    def _records_to_checkpoint(
        self,
        branch: BranchManifest,
        checkpoint_id: str,
    ) -> tuple[SimulationLogRecord, ...]:
        del branch
        return tuple(
            record
            for record in self.logs.reachable(self.checkpoints, checkpoint_id)
            if record.result.resolved_turn is not None
        )

    def _branch_ids(self, branch: BranchManifest) -> tuple[str, ...]:
        ids: list[str] = []
        seen: set[str] = set()
        current = branch
        while current.branch_id not in seen:
            seen.add(current.branch_id)
            ids.append(current.branch_id)
            if current.parent_branch_id is None:
                return tuple(ids)
            current = self.branches.load(current.parent_branch_id)
        raise ValueError("branch ancestry contains a cycle")

    def _wiki_version(
        self,
        branch: BranchManifest,
        checkpoint_id: str,
    ) -> tuple[str, str]:
        branch_ids = self._branch_ids(branch)
        for candidate in reversed(self.checkpoints.lineage(checkpoint_id)):
            for branch_id in branch_ids:
                store = WikiContextBuilder(self.root, branch_id).store
                if store.version_exists(candidate):
                    return branch_id, candidate
        for branch_id in branch_ids:
            store = WikiContextBuilder(self.root, branch_id).store
            if store.version_exists("seed"):
                return branch_id, "seed"
        raise ValueError("selected checkpoint has no reachable Wiki version")

    def _director_instruction_ids(self, branch: BranchManifest) -> frozenset[str]:
        return frozenset(
            instruction.instruction_id
            for branch_id in self._branch_ids(branch)
            for instruction in WikiContextBuilder(
                self.root,
                branch_id,
            ).store.list_instructions()
        )

    @staticmethod
    def _events(
        records: tuple[SimulationLogRecord, ...],
    ) -> tuple[ResolvedEvent, ...]:
        return tuple(
            event
            for record in records
            if record.result.resolved_turn is not None
            for event in record.result.resolved_turn.events
        )

    @staticmethod
    def _events_for_viewpoint(
        events: tuple[ResolvedEvent, ...],
        viewpoint_actor_id: str | None,
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

    def list_sources(
        self,
        branch_id: str,
        *,
        after_step: int | None = None,
    ) -> tuple[NarrativeSourceSummary, ...]:
        branch = self._branch(branch_id)
        checkpoint_id, snapshot = self._select_checkpoint(branch, None)
        records = self._records_to_checkpoint(branch, checkpoint_id)
        if not records:
            return ()

        ranges: list[tuple[int, int, SimulationBoundary]] = []
        range_start = records[0].result.step
        for record in records:
            boundary = record.result.boundary
            if boundary != SimulationBoundary.NONE:
                ranges.append((range_start, record.result.step, boundary))
                range_start = record.result.step + 1
        if range_start <= records[-1].result.step:
            ranges.append(
                (range_start, records[-1].result.step, SimulationBoundary.NONE)
            )

        source_statuses = self._source_statuses(branch_id)
        summaries = []
        for from_step, to_step, boundary in ranges:
            if after_step is not None and to_step <= after_step:
                continue
            selected = tuple(
                record
                for record in records
                if from_step <= record.result.step <= to_step
            )
            events = self._events(selected)
            if not events:
                continue
            source_checkpoint_id = selected[-1].checkpoint_id or checkpoint_id
            summary_text = "\n".join(event.event_text for event in events)
            first_line = events[0].event_text.splitlines()[0].strip()
            summaries.append(
                NarrativeSourceSummary(
                    source_id=f"source:{branch_id}:{from_step}:{to_step}",
                    branch_id=branch_id,
                    checkpoint_id=source_checkpoint_id,
                    from_step=from_step,
                    to_step=to_step,
                    boundary=boundary,
                    title_hint=first_line[:120] or f"Steps {from_step}-{to_step}",
                    event_summary_text=summary_text,
                    available_viewpoint_ids=tuple(snapshot.roster_actor_ids),
                    status=source_statuses.get(
                        (from_step, to_step),
                        "available",
                    ),
                )
            )
        return tuple(summaries)

    def build_source(
        self,
        *,
        branch_id: str,
        checkpoint_id: str,
        from_step: int,
        to_step: int,
        viewpoint_actor_id: str | None,
    ) -> NarrativeSource:
        branch = self._branch(branch_id)
        selected_checkpoint, snapshot = self._select_checkpoint(
            branch,
            checkpoint_id,
        )
        if to_step < from_step:
            raise ValueError("narrative source step range is reversed")
        records = tuple(
            record
            for record in self._records_to_checkpoint(branch, selected_checkpoint)
            if from_step <= record.result.step <= to_step
        )
        events = self._events(records)
        if not events:
            raise ValueError("narrative source contains no resolved events")
        if viewpoint_actor_id is None:
            counts = Counter(
                record.result.resolved_turn.acting_actor_id
                for record in records
                if record.result.resolved_turn is not None
                and record.result.resolved_turn.acting_actor_id is not None
            )
            viewpoint_actor_id = counts.most_common(1)[0][0] if counts else None
        if viewpoint_actor_id is not None and (
            viewpoint_actor_id not in snapshot.roster_actor_ids
            or viewpoint_actor_id not in snapshot.memory_snapshots
        ):
            raise ValueError("viewpoint actor is unavailable at this checkpoint")
        memories = self._relevant_memories(
            snapshot,
            from_step=from_step,
            to_step=to_step,
            source_memory_ids={
                memory_id for event in events for memory_id in event.source_memory_ids
            },
        )
        boundary = records[-1].result.boundary
        wiki_branch_id, wiki_version_id = self._wiki_version(
            branch,
            selected_checkpoint,
        )
        return NarrativeSource(
            project_id=branch.project_id,
            branch_id=branch_id,
            checkpoint_id=selected_checkpoint,
            from_step=from_step,
            to_step=to_step,
            boundary=boundary,
            event_ids=tuple(event.event_id for event in events),
            memory_record_ids=tuple(record.record_id for record in memories),
            wiki_branch_id=wiki_branch_id,
            wiki_version_id=wiki_version_id,
            viewpoint_actor_id=viewpoint_actor_id,
            content_locale=snapshot.content_locale,
        )

    def _load_facts(
        self,
        source: NarrativeSource,
    ) -> tuple[BranchManifest, tuple[ResolvedEvent, ...], tuple[MemoryRecord, ...]]:
        branch = self._branch(source.branch_id)
        if branch.project_id != source.project_id:
            raise ValueError("narrative source project does not match branch")
        _, snapshot = self._select_checkpoint(branch, source.checkpoint_id)
        records = tuple(
            record
            for record in self._records_to_checkpoint(branch, source.checkpoint_id)
            if source.from_step <= record.result.step <= source.to_step
        )
        events = self._events(records)
        if tuple(event.event_id for event in events) != source.event_ids:
            raise ValueError("narrative source events changed")
        memories = self._relevant_memories(
            snapshot,
            from_step=source.from_step,
            to_step=source.to_step,
            source_memory_ids=set(source.memory_record_ids),
        )
        by_id = {record.record_id: record for record in memories}
        selected_memories = tuple(by_id[item] for item in source.memory_record_ids)
        return branch, events, selected_memories

    @staticmethod
    def _routing_ids(
        events: tuple[ResolvedEvent, ...],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
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
        return participants, locations

    def load_writer_context(self, source: NarrativeSource) -> WriterContext:
        branch, events, memories = self._load_facts(source)
        writer_events = self._events_for_viewpoint(
            events,
            source.viewpoint_actor_id,
        )
        if not writer_events:
            raise ValueError("Writer context contains no visible resolved events")
        viewpoint = tuple(
            record
            for record in memories
            if source.viewpoint_actor_id is not None
            and record.scope == MemoryScope.CHARACTER
            and record.owner_id == source.viewpoint_actor_id
            and record.record_type
            not in {
                MemoryRecordType.PLAN,
                MemoryRecordType.PUTATIVE_EVENT,
                MemoryRecordType.SYSTEM,
            }
        )
        participants, locations = self._routing_ids(writer_events)
        wiki_context = WikiContextBuilder(
            self.root,
            source.wiki_branch_id,
            version_id=source.wiki_version_id,
            excluded_source_ids=self._director_instruction_ids(branch),
        ).writer(
            source.viewpoint_actor_id,
            participant_ids=participants,
            location_ids=locations,
            keywords=tuple(event.event_text for event in writer_events),
        )
        return WriterContext(
            source=source,
            events=writer_events,
            viewpoint_memories=viewpoint,
            world_wiki_context=wiki_context.content,
            wiki_context_manifest=wiki_context.manifest,
        )

    def load_editor_context(self, source: NarrativeSource) -> EditorContext:
        branch, events, memories = self._load_facts(source)
        participants, locations = self._routing_ids(events)
        wiki_context = WikiContextBuilder(
            self.root,
            source.wiki_branch_id,
            version_id=source.wiki_version_id,
            excluded_source_ids=self._director_instruction_ids(branch),
        ).editor(
            participant_ids=participants,
            location_ids=locations,
            keywords=tuple(event.event_text for event in events),
        )
        return EditorContext(
            source=source,
            events=events,
            memories=memories,
            world_wiki_context=wiki_context.content,
            wiki_context_manifest=wiki_context.manifest,
        )

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

    def _relevant_memories(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        from_step: int,
        to_step: int,
        source_memory_ids: set[str],
    ) -> tuple[MemoryRecord, ...]:
        selected = []
        for record in self._decode_snapshot_memories(snapshot):
            is_relevant = (
                from_step <= record.step <= to_step
                or record.record_id in source_memory_ids
            )
            if is_relevant:
                selected.append(record)
        selected.sort(key=lambda item: (item.step, item.created_at, item.record_id))
        return tuple(selected)

    def _source_statuses(
        self,
        branch_id: str,
    ) -> dict[tuple[int, int], NarrativeSourceStatus]:
        directory = self.root / ".story-engine/manuscript" / branch_id / "drafts"
        statuses: dict[tuple[int, int], NarrativeSourceStatus] = {}
        if not directory.exists():
            return statuses
        for path in directory.glob("*.md"):
            try:
                payload = load_json_envelope(
                    path,
                    schema="story-engine/scene-draft/v1",
                )
                key = (
                    int(payload["source_from_step"]),
                    int(payload["source_to_step"]),
                )
                draft_status = str(payload["status"])
                status: NarrativeSourceStatus = (
                    "needs_revision"
                    if draft_status == "needs_revision"
                    else "saved"
                    if draft_status == "saved"
                    else "drafted"
                )
                statuses[key] = status
            except (KeyError, OSError, TypeError, ValueError):
                continue
        return statuses
