from pathlib import Path

from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.narrative import (
    NarrativeContext,
    NarrativeSource,
    NarrativeSourceSummary,
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

    def _ancestry_records(
        self,
        branch: BranchManifest,
    ) -> tuple[SimulationLogRecord, ...]:
        inherited: tuple[SimulationLogRecord, ...] = ()
        if branch.parent_branch_id and branch.fork_checkpoint_id:
            parent = self.branches.load(branch.parent_branch_id)
            fork = self.checkpoints.load(branch.fork_checkpoint_id)
            inherited = tuple(
                record
                for record in self._ancestry_records(parent)
                if record.result.step < fork.current_step
            )
        combined = (*inherited, *self.logs.read(branch.branch_id))
        by_trace = {record.trace.trace_id: record for record in combined}
        return tuple(
            sorted(
                by_trace.values(),
                key=lambda item: (item.result.step, item.trace.started_at),
            )
        )

    def _select_checkpoint(
        self,
        branch: BranchManifest,
        checkpoint_id: str | None,
    ) -> tuple[str, TurnSessionSnapshot]:
        selected = checkpoint_id or branch.head_checkpoint_id
        if selected is None:
            raise ValueError("branch has no checkpoint")
        reachable = {
            branch.head_checkpoint_id,
            branch.fork_checkpoint_id,
            *(
                record.checkpoint_id
                for record in self._ancestry_records(branch)
                if record.checkpoint_id is not None
            ),
        }
        if selected not in reachable:
            raise ValueError("checkpoint is not part of the selected branch history")
        snapshot = self.checkpoints.load(selected)
        if snapshot.project_id != branch.project_id:
            raise ValueError("checkpoint belongs to another project")
        return selected, snapshot

    def _records_to_checkpoint(
        self,
        branch: BranchManifest,
        snapshot: TurnSessionSnapshot,
    ) -> tuple[SimulationLogRecord, ...]:
        return tuple(
            record
            for record in self._ancestry_records(branch)
            if record.result.step < snapshot.current_step
            and record.result.resolved_turn is not None
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
            return events
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
        records = self._records_to_checkpoint(branch, snapshot)
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

        written_ranges = self._written_ranges(branch_id)
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
                    available_viewpoint_ids=tuple(snapshot.active_entity_ids),
                    already_written=(from_step, to_step) in written_ranges,
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
            for record in self._records_to_checkpoint(branch, snapshot)
            if from_step <= record.result.step <= to_step
        )
        events = self._events_for_viewpoint(
            self._events(records),
            viewpoint_actor_id,
        )
        if not events:
            raise ValueError("narrative source contains no resolved events")
        if viewpoint_actor_id is not None and (
            viewpoint_actor_id not in snapshot.active_entity_ids
            or viewpoint_actor_id not in snapshot.memory_snapshots
        ):
            raise ValueError("viewpoint actor is unavailable at this checkpoint")
        memories = self._selected_memories(
            snapshot,
            from_step=from_step,
            to_step=to_step,
            viewpoint_actor_id=viewpoint_actor_id,
            source_memory_ids={
                memory_id for event in events for memory_id in event.source_memory_ids
            },
        )
        boundary = records[-1].result.boundary
        return NarrativeSource(
            project_id=branch.project_id,
            branch_id=branch_id,
            checkpoint_id=selected_checkpoint,
            from_step=from_step,
            to_step=to_step,
            boundary=boundary,
            event_ids=tuple(event.event_id for event in events),
            memory_record_ids=tuple(record.record_id for record in memories),
            viewpoint_actor_id=viewpoint_actor_id,
            content_locale=snapshot.content_locale,
        )

    def load_source(self, source: NarrativeSource) -> NarrativeContext:
        branch = self._branch(source.branch_id)
        if branch.project_id != source.project_id:
            raise ValueError("narrative source project does not match branch")
        _, snapshot = self._select_checkpoint(branch, source.checkpoint_id)
        records = tuple(
            record
            for record in self._records_to_checkpoint(branch, snapshot)
            if source.from_step <= record.result.step <= source.to_step
        )
        events = self._events_for_viewpoint(
            self._events(records),
            source.viewpoint_actor_id,
        )
        if tuple(event.event_id for event in events) != source.event_ids:
            raise ValueError("narrative source events changed")
        memories = self._selected_memories(
            snapshot,
            from_step=source.from_step,
            to_step=source.to_step,
            viewpoint_actor_id=source.viewpoint_actor_id,
            source_memory_ids=set(source.memory_record_ids),
        )
        by_id = {record.record_id: record for record in memories}
        selected_memories = tuple(by_id[item] for item in source.memory_record_ids)
        game_master = tuple(
            item for item in selected_memories if item.scope == MemoryScope.GAME_MASTER
        )
        viewpoint = tuple(
            item for item in selected_memories if item.scope == MemoryScope.CHARACTER
        )
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
        wiki_context = WikiContextBuilder(
            self.root,
            source.branch_id,
        ).writer(
            source.viewpoint_actor_id,
            participant_ids=participants,
            location_ids=locations,
            keywords=tuple(event.event_text for event in events),
        )
        return NarrativeContext(
            source=source,
            events=events,
            game_master_memories=game_master,
            viewpoint_memories=viewpoint,
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

    def _selected_memories(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        from_step: int,
        to_step: int,
        viewpoint_actor_id: str | None,
        source_memory_ids: set[str],
    ) -> tuple[MemoryRecord, ...]:
        selected = []
        for record in self._decode_snapshot_memories(snapshot):
            if viewpoint_actor_id is None:
                is_allowed_owner = record.scope == MemoryScope.GAME_MASTER
            elif record.scope == MemoryScope.CHARACTER:
                is_allowed_owner = record.owner_id == viewpoint_actor_id
            else:
                is_allowed_owner = (
                    record.scope == MemoryScope.GAME_MASTER
                    and record.record_type == MemoryRecordType.PREMISE
                    and "private" not in record.tags
                    and "secret" not in record.tags
                )
            is_relevant = (
                from_step <= record.step <= to_step
                or record.record_id in source_memory_ids
            )
            if is_allowed_owner and is_relevant:
                selected.append(record)
        selected.sort(key=lambda item: (item.step, item.created_at, item.record_id))
        return tuple(selected)

    def _written_ranges(self, branch_id: str) -> set[tuple[int, int]]:
        directory = self.root / ".story-engine/manuscript" / branch_id / "drafts"
        ranges: set[tuple[int, int]] = set()
        if not directory.exists():
            return ranges
        for path in directory.glob("*.md"):
            try:
                payload = load_json_envelope(
                    path,
                    schema="story-engine/scene-draft/v1",
                )
                ranges.add(
                    (int(payload["source_from_step"]), int(payload["source_to_step"]))
                )
            except (KeyError, OSError, TypeError, ValueError):
                continue
        return ranges
