from collections.abc import Iterable
from pathlib import Path

from story_engine.domain.memory import MemoryRecordType, MemoryScope
from story_engine.domain.projection import SimulationBoundary
from story_engine.domain.simulation import BranchManifest, TurnSessionSnapshot
from story_engine.domain.wiki import WikiPatch, WikiSourceKind
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.simulation_log import (
    SimulationLogRecord,
    SimulationLogStore,
)
from story_engine.wiki.consolidator import WikiConsolidator
from story_engine.wiki.lint import WikiLinter
from story_engine.wiki.reader import WikiSourceReader
from story_engine.wiki.store import WikiStore
from story_engine.wiki.validator import WikiValidator


def branch_records(root: Path, branch_id: str) -> tuple[SimulationLogRecord, ...]:
    branches = BranchStore(root)
    checkpoints = CheckpointStore(root)
    logs = SimulationLogStore(root)

    def read(branch: BranchManifest) -> tuple[SimulationLogRecord, ...]:
        inherited: tuple[SimulationLogRecord, ...] = ()
        if branch.parent_branch_id and branch.fork_checkpoint_id:
            parent = branches.load(branch.parent_branch_id)
            fork = checkpoints.load(branch.fork_checkpoint_id)
            inherited = tuple(
                record
                for record in read(parent)
                if record.result.step < fork.current_step
            )
        return (*inherited, *logs.read(branch.branch_id))

    records = read(branches.load(branch_id))
    by_trace = {record.trace.trace_id: record for record in records}
    return tuple(
        sorted(
            by_trace.values(),
            key=lambda item: (item.result.step, item.trace.started_at),
        )
    )


def _scene_records(
    records: tuple[SimulationLogRecord, ...],
    end_step: int,
) -> tuple[SimulationLogRecord, ...]:
    selected: list[SimulationLogRecord] = []
    for record in reversed(records):
        if record.result.step > end_step:
            continue
        if selected and record.result.boundary != SimulationBoundary.NONE:
            break
        selected.append(record)
    return tuple(reversed(selected))


class WikiBoundaryProcessor:
    """Consolidate all boundary observers after the runtime commit succeeds."""

    def __init__(
        self,
        root: Path,
        *,
        consolidator: WikiConsolidator,
        validator: WikiValidator | None = None,
    ) -> None:
        self.root = root
        self.consolidator = consolidator
        self.validator = validator or WikiValidator()

    async def process(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        boundary: SimulationBoundary,
        end_step: int,
        records: tuple[SimulationLogRecord, ...] | None = None,
    ) -> tuple[Path, ...]:
        if boundary == SimulationBoundary.NONE:
            return ()
        if snapshot.checkpoint_id is None:
            raise ValueError("Wiki maintenance requires a durable checkpoint")
        store = WikiStore(self.root, snapshot.branch_id)
        source_reader = WikiSourceReader(self.root, snapshot.branch_id)
        all_records = records or branch_records(self.root, snapshot.branch_id)
        scene_records = _scene_records(all_records, end_step)
        if not scene_records:
            return ()
        first_step = scene_records[0].result.step
        memories = SimulationLogStore(self.root).read_observations(snapshot.branch_id)
        affected_subjects = tuple(
            sorted(
                {
                    memory.owner_id
                    for memory in memories
                    if memory.scope == MemoryScope.CHARACTER
                    and memory.record_type == MemoryRecordType.OBSERVATION
                    and first_step <= memory.step <= end_step
                }
            )
        )
        existing_pages = store.list_pages()
        all_patches: list[WikiPatch] = []

        world_sources = tuple(
            source
            for source in source_reader.world_sources(
                records=scene_records,
                snapshot=snapshot,
            )
            if source.step >= first_step
            or source.kind
            in {
                WikiSourceKind.PROJECT,
                WikiSourceKind.DIRECTOR_INSTRUCTION,
            }
        )
        world_pages = tuple(
            page for page in existing_pages if page.path.startswith("world/")
        )
        world_patches = await self.consolidator.consolidate(
            branch_id=snapshot.branch_id,
            subject_id=None,
            pages=world_pages,
            sources=world_sources,
            content_locale=snapshot.content_locale,
        )
        self.validator.validate(
            world_patches,
            branch_id=snapshot.branch_id,
            sources=world_sources,
            subject_id=None,
            existing_paths={page.path for page in existing_pages},
        )
        all_patches.extend(world_patches)

        for subject_id in affected_subjects:
            sources = tuple(
                source
                for source in source_reader.character_sources(
                    subject_id,
                    records=scene_records,
                    snapshot=snapshot,
                )
                if source.step >= first_step
                or source.kind == WikiSourceKind.PROFILE
            )
            pages = tuple(
                page
                for page in existing_pages
                if page.path.startswith(f"characters/{subject_id}/")
            )
            patches = await self.consolidator.consolidate(
                branch_id=snapshot.branch_id,
                subject_id=subject_id,
                pages=pages,
                sources=sources,
                content_locale=snapshot.content_locale,
            )
            self.validator.validate(
                patches,
                branch_id=snapshot.branch_id,
                sources=sources,
                subject_id=subject_id,
                existing_paths={
                    *{page.path for page in existing_pages},
                    *{patch.path for patch in all_patches},
                },
            )
            all_patches.extend(patches)

        written = store.apply_patches(
            tuple(all_patches),
            checkpoint_id=snapshot.checkpoint_id,
            step=end_step,
        )
        if not all_patches:
            store.set_head(snapshot.checkpoint_id, end_step, stale=False)
        if boundary == SimulationBoundary.CHAPTER:
            lint = WikiLinter(self.root, snapshot.branch_id).run()
            if not lint.passed:
                details = "; ".join(issue.message for issue in lint.issues)
                raise ValueError(f"Wiki lint failed: {details}")
        return written

    async def rebuild(
        self,
        snapshot: TurnSessionSnapshot,
        records: Iterable[SimulationLogRecord] | None = None,
    ) -> tuple[Path, ...]:
        selected = tuple(records or branch_records(self.root, snapshot.branch_id))
        written: list[Path] = []
        for record in selected:
            if record.result.step >= snapshot.current_step:
                break
            if record.result.boundary == SimulationBoundary.NONE:
                continue
            written.extend(
                await self.process(
                    snapshot,
                    boundary=record.result.boundary,
                    end_step=record.result.step,
                    records=selected,
                )
            )
        if snapshot.checkpoint_id is not None:
            WikiStore(self.root, snapshot.branch_id).set_head(
                snapshot.checkpoint_id,
                snapshot.current_step,
                stale=False,
            )
        return tuple(written)
