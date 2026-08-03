from pathlib import Path

from story_engine.domain.simulation import (
    BranchManifest,
    CommitResult,
    StepResult,
    TurnSessionSnapshot,
)
from story_engine.domain.trace import TurnTrace
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.session_store import SessionStore
from story_engine.persistence.simulation_log import (
    SimulationLogRecord,
    SimulationLogStore,
)
from story_engine.projection.markdown import MarkdownProjector
from story_engine.wiki.store import WikiStore


class SimulationCommitKernel:
    """Persist checkpoint/log before atomically advancing a branch head."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.checkpoints = CheckpointStore(root)
        self.branches = BranchStore(root)
        self.logs = SimulationLogStore(root)
        self.sessions = SessionStore(root)
        self.projector = MarkdownProjector(root)

    def save_checkpoint(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        reason: str,
    ) -> CommitResult:
        del reason
        branch = self.branches.ensure(
            branch_id=snapshot.branch_id,
            project_id=snapshot.project_id,
            content_locale=snapshot.content_locale,
        )
        checkpoint_id, checkpoint_path = self.checkpoints.save(snapshot)
        updated = self.branches.advance(
            snapshot.branch_id,
            checkpoint_id=checkpoint_id,
            step=snapshot.current_step,
            expected_head_checkpoint_id=branch.head_checkpoint_id,
        )
        return CommitResult(
            branch=updated,
            checkpoint_id=checkpoint_id,
            session_id=snapshot.session_id,
            step=snapshot.current_step,
            state_hash=snapshot.state_hash,
            written_paths=(
                str(checkpoint_path),
                str(self.branches.path_for(branch.branch_id)),
            ),
        )

    def append_step(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
        trace: TurnTrace,
        *,
        checkpoint: bool = True,
    ) -> CommitResult | None:
        branch = self.branches.ensure(
            branch_id=snapshot.branch_id,
            project_id=snapshot.project_id,
            content_locale=snapshot.content_locale,
        )
        checkpoint_id: str | None = None
        checkpoint_path: Path | None = None
        if checkpoint:
            checkpoint_id, checkpoint_path = self.checkpoints.save(snapshot)
        log_path = self.logs.append(
            snapshot.branch_id,
            SimulationLogRecord(
                checkpoint_id=checkpoint_id,
                state_hash=snapshot.state_hash,
                result=result.model_copy(update={"checkpoint_id": checkpoint_id}),
                trace=trace,
            ),
        )
        if checkpoint_id is None or checkpoint_path is None:
            return None
        updated = self.branches.advance(
            snapshot.branch_id,
            checkpoint_id=checkpoint_id,
            step=snapshot.current_step,
            expected_head_checkpoint_id=branch.head_checkpoint_id,
        )
        return CommitResult(
            branch=updated,
            checkpoint_id=checkpoint_id,
            session_id=snapshot.session_id,
            step=snapshot.current_step,
            state_hash=snapshot.state_hash,
            written_paths=(str(checkpoint_path), str(log_path)),
        )

    def load_checkpoint(
        self,
        project_id: str,
        checkpoint_id: str,
    ) -> TurnSessionSnapshot:
        snapshot = self.checkpoints.load(checkpoint_id)
        if snapshot.project_id != project_id:
            raise ValueError("checkpoint belongs to another project")
        return snapshot

    def create_branch(
        self,
        project_id: str,
        *,
        source_checkpoint_id: str,
        branch_id: str,
        parent_branch_id: str,
        content_locale: str,
    ) -> BranchManifest:
        branch = self.branches.create(
            branch_id=branch_id,
            project_id=project_id,
            source_checkpoint_id=source_checkpoint_id,
            parent_branch_id=parent_branch_id,
            content_locale=content_locale,
        )
        parent_wiki = WikiStore(self.root, parent_branch_id)
        if parent_wiki.exists():
            source = self.checkpoints.load(source_checkpoint_id)
            WikiStore(self.root, branch_id).fork_from(
                parent_branch_id=parent_branch_id,
                checkpoint_id=source_checkpoint_id,
                checkpoint_step=source.current_step,
            )
        return branch

    def rollback_branch(
        self,
        project_id: str,
        branch_id: str,
        *,
        checkpoint_id: str,
    ) -> BranchManifest:
        branch = self.branches.load(branch_id)
        if branch.project_id != project_id:
            raise ValueError("branch belongs to another project")
        updated = self.branches.rollback(branch_id, checkpoint_id)
        snapshot = self.checkpoints.load(checkpoint_id)
        wiki = WikiStore(self.root, branch_id)
        if wiki.exists():
            wiki.mark_stale(checkpoint_id, snapshot.current_step)
        return updated

    def project_markdown(
        self,
        project_id: str,
        branch_id: str,
        *,
        checkpoint_id: str | None = None,
    ) -> tuple[Path, ...]:
        branch = self.branches.load(branch_id)
        if branch.project_id != project_id:
            raise ValueError("branch belongs to another project")
        selected = checkpoint_id or branch.head_checkpoint_id
        if selected is None:
            raise ValueError("branch has no checkpoint")
        snapshot = self.load_checkpoint(project_id, selected)
        if (self.root / "project.md").is_file():
            del snapshot
            store = WikiStore(self.root, branch_id)
            return tuple(store.branch_root.rglob("*.md"))
        return self.projector.render(
            snapshot,
            self.logs.read(branch_id),
            branch_id=branch_id,
        )
