from collections.abc import Callable
from pathlib import Path

from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.projection import SimulationBoundary
from story_engine.domain.simulation import TurnSessionSnapshot
from story_engine.domain.wiki import (
    WikiPage,
    WikiPatch,
    WikiPatchOperation,
    WikiSourceKind,
)
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.simulation_log import (
    SimulationLogRecord,
    SimulationLogStore,
)
from story_engine.wiki.consolidator import WikiConsolidator
from story_engine.wiki.lint import WikiLinter
from story_engine.wiki.reader import WikiSourceReader
from story_engine.wiki.store import WikiRevisionConflictError, WikiStore
from story_engine.wiki.validator import WikiValidator


def branch_records(root: Path, branch_id: str) -> tuple[SimulationLogRecord, ...]:
    branches = BranchStore(root)
    checkpoints = CheckpointStore(root)
    logs = SimulationLogStore(root)
    branch = branches.load(branch_id)
    if branch.head_checkpoint_id is None:
        return ()
    # Checkpoint lineage is the authority for reachable history. Scanning branch
    # logs by step range would reintroduce abandoned turns after a rollback.
    return logs.reachable(
        checkpoints,
        branch.head_checkpoint_id,
        branch_id=branch_id,
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
    ) -> tuple[Path, ...]:
        if snapshot.checkpoint_id is None:
            raise ValueError("Wiki maintenance requires a durable checkpoint")
        checkpoint_id = snapshot.checkpoint_id
        branches = BranchStore(self.root)
        checkpoints = CheckpointStore(self.root)
        branches.assert_checkpoint_reachable(snapshot.branch_id, checkpoint_id)
        logs = SimulationLogStore(self.root)
        records = logs.reachable(
            checkpoints,
            checkpoint_id,
            branch_id=snapshot.branch_id,
        )
        memories = logs.reachable_memory_records(
            checkpoints,
            checkpoint_id,
            branch_id=snapshot.branch_id,
        )
        return await self._process_with_sources(
            snapshot,
            boundary=boundary,
            end_step=end_step,
            records=records,
            memory_records=memories,
            precondition=lambda: branches.assert_checkpoint_reachable(
                snapshot.branch_id,
                checkpoint_id,
            ),
        )

    async def _process_with_sources(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        boundary: SimulationBoundary,
        end_step: int,
        records: tuple[SimulationLogRecord, ...],
        memory_records: tuple[MemoryRecord, ...] = (),
        precondition: Callable[[], None] | None = None,
    ) -> tuple[Path, ...]:
        """Test seam; production callers must use checkpoint-authoritative process."""

        if boundary == SimulationBoundary.NONE:
            return ()
        if snapshot.checkpoint_id is None:
            raise ValueError("Wiki maintenance requires a durable checkpoint")
        checkpoint_id = snapshot.checkpoint_id
        store = WikiStore(self.root, snapshot.branch_id)
        source_reader = WikiSourceReader(self.root, snapshot.branch_id)
        scene_records = _scene_records(records, end_step)
        if not scene_records:
            return ()
        first_step = scene_records[0].result.step
        memories = memory_records or tuple(
            memory for record in records for memory in record.memory_delta
        )
        scene_memory_ids = {
            memory.record_id
            for record in scene_records
            for memory in record.memory_delta
        }
        knowledge_subject_ids = {
            character.id
            for character in snapshot.characters
            if character.type in {"active", "retired"}
        }
        affected_subjects = tuple(
            sorted(
                {
                    memory.owner_id
                    for memory in memories
                    if memory.scope == MemoryScope.CHARACTER
                    and memory.record_type == MemoryRecordType.OBSERVATION
                    and memory.owner_id in knowledge_subject_ids
                    and memory.record_id in scene_memory_ids
                    and first_step <= memory.step <= end_step
                }
            )
        )

        async def consolidate_and_apply(
            existing_pages: tuple[WikiPage, ...],
        ) -> tuple[Path, ...]:
            pages_by_path = {page.path: page for page in existing_pages}
            all_patches: list[WikiPatch] = []

            world_pages = tuple(
                page for page in existing_pages if page.path.startswith("world/")
            )
            world_sources = tuple(
                source
                for source in source_reader.world_sources(
                    records=scene_records,
                    memories=memories,
                )
                if source.step >= first_step
                or source.kind
                in {
                    WikiSourceKind.PROJECT,
                    WikiSourceKind.DIRECTOR_INSTRUCTION,
                }
            )
            world_patches = await self.consolidator.consolidate(
                project_id=snapshot.project_id,
                session_id=snapshot.session_id,
                step=end_step,
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
                existing_paths=set(pages_by_path),
            )
            all_patches.extend(world_patches)

            for subject_id in affected_subjects:
                pages = tuple(
                    page
                    for page in existing_pages
                    if page.path.startswith(f"characters/{subject_id}/")
                )
                sources = tuple(
                    source
                    for source in source_reader.character_sources(
                        subject_id,
                        records=scene_records,
                        memories=memories,
                    )
                    if source.step >= first_step
                    or source.kind
                    in {
                        WikiSourceKind.PROJECT,
                        WikiSourceKind.PROFILE,
                    }
                )
                patches = await self.consolidator.consolidate(
                    project_id=snapshot.project_id,
                    session_id=snapshot.session_id,
                    step=end_step,
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
                        *pages_by_path,
                        *{patch.path for patch in all_patches},
                    },
                )
                all_patches.extend(patches)

            enriched = self._with_expected_revision(
                tuple(all_patches),
                pages_by_path,
            )
            written = store.apply_patches(
                enriched,
                checkpoint_id=checkpoint_id,
                step=end_step,
                precondition=precondition,
            )
            if not all_patches:
                store.set_head(
                    checkpoint_id,
                    end_step,
                    stale=False,
                    precondition=precondition,
                )
            return written

        try:
            written = await consolidate_and_apply(store.model_editable_pages())
        except WikiRevisionConflictError:
            written = await consolidate_and_apply(store.model_editable_pages())
        if boundary == SimulationBoundary.CHAPTER:
            lint = WikiLinter(self.root, snapshot.branch_id).run()
            if not lint.passed:
                details = "; ".join(issue.message for issue in lint.issues)
                raise ValueError(f"Wiki lint failed: {details}")
        return written

    @staticmethod
    def _with_expected_revision(
        patches: tuple[WikiPatch, ...],
        pages_by_path: dict[str, WikiPage],
    ) -> tuple[WikiPatch, ...]:
        enriched: list[WikiPatch] = []
        for patch in patches:
            if patch.operation == WikiPatchOperation.CREATE:
                enriched.append(patch)
                continue
            current = pages_by_path.get(patch.path)
            if current is None:
                raise ValueError(f"wiki patch targets missing page {patch.path!r}")
            enriched.append(
                patch.model_copy(
                    update={
                        "expected_revision": current.revision,
                        "expected_content_hash": current.content_hash,
                    }
                )
            )
        return tuple(enriched)

    async def rebuild(
        self,
        snapshot: TurnSessionSnapshot,
    ) -> tuple[Path, ...]:
        if snapshot.checkpoint_id is None:
            raise ValueError("Wiki rebuild requires a durable checkpoint")
        target_checkpoint_id = snapshot.checkpoint_id
        branches = BranchStore(self.root)
        checkpoints = CheckpointStore(self.root)
        logs = SimulationLogStore(self.root)

        def assert_current_target() -> None:
            branches.assert_head(snapshot.branch_id, target_checkpoint_id)

        assert_current_target()
        lineage = checkpoints.lineage(target_checkpoint_id)
        store = WikiStore(self.root, snapshot.branch_id)
        baseline_version = next(
            (
                checkpoint_id
                for checkpoint_id in reversed(lineage)
                if store.version_exists(checkpoint_id)
            ),
            "seed",
        )
        store.restore_version(baseline_version, precondition=assert_current_target)
        selected = logs.reachable(
            checkpoints,
            target_checkpoint_id,
            branch_id=snapshot.branch_id,
        )
        baseline_index = (
            lineage.index(baseline_version) if baseline_version != "seed" else -1
        )
        written: list[Path] = []
        for record in selected:
            if record.result.step >= snapshot.current_step:
                break
            if record.checkpoint_id is None:
                continue
            if lineage.index(record.checkpoint_id) <= baseline_index:
                continue
            if record.result.boundary == SimulationBoundary.NONE:
                continue
            boundary_snapshot = checkpoints.load(record.checkpoint_id)
            if boundary_snapshot.branch_id != snapshot.branch_id:
                boundary_snapshot = boundary_snapshot.model_copy(
                    update={
                        "branch_id": snapshot.branch_id,
                        "request": boundary_snapshot.request.model_copy(
                            update={"branch_id": snapshot.branch_id}
                        ),
                    }
                )
            boundary_records = logs.reachable(
                checkpoints,
                record.checkpoint_id,
                branch_id=snapshot.branch_id,
            )
            memories = logs.reachable_memory_records(
                checkpoints,
                record.checkpoint_id,
                branch_id=snapshot.branch_id,
            )
            written.extend(
                await self._process_with_sources(
                    boundary_snapshot,
                    boundary=record.result.boundary,
                    end_step=record.result.step,
                    records=boundary_records,
                    memory_records=memories,
                    precondition=assert_current_target,
                )
            )
        store.set_head(
            target_checkpoint_id,
            snapshot.current_step,
            stale=False,
            precondition=assert_current_target,
        )
        return tuple(written)
