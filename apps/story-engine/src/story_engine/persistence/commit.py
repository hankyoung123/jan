from pathlib import Path

from story_engine.domain.session_manifest import SessionManifest
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
from story_engine.workspace.transaction import AtomicBatch


class SimulationCommitKernel:
    """Own recoverable all-or-nothing persistence for a simulation step."""

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
        checkpoint_id, checkpoint_path, checkpoint_content = self.checkpoints.prepare(
            snapshot
        )
        updated, branch_path, branch_content = self.branches.prepare_advance(
            snapshot.branch_id,
            checkpoint_id=checkpoint_id,
            step=snapshot.current_step,
            expected_head_checkpoint_id=branch.head_checkpoint_id,
        )
        persisted = snapshot.model_copy(update={"checkpoint_id": checkpoint_id})
        manifest = SessionManifest.from_snapshot(persisted)
        session_path, session_content = self.sessions.prepare(manifest)
        batch = AtomicBatch(self.root)
        written: list[Path] = []
        if checkpoint_path.exists():
            if checkpoint_path.read_text(encoding="utf-8") != checkpoint_content:
                raise ValueError("checkpoint content hash collision")
        else:
            batch.add(
                self._relative(checkpoint_path),
                checkpoint_content,
                overwrite=False,
            )
            written.append(checkpoint_path)
        batch.add(self._relative(session_path), session_content)
        written.append(session_path)
        batch.add(self._relative(branch_path), branch_content)
        written.append(branch_path)
        batch.commit(
            precondition=lambda: self.branches.assert_head(
                snapshot.branch_id,
                branch.head_checkpoint_id,
            )
        )
        return CommitResult(
            branch=updated,
            checkpoint_id=checkpoint_id,
            session_id=snapshot.session_id,
            step=snapshot.current_step,
            state_hash=snapshot.state_hash,
            written_paths=tuple(str(path) for path in written),
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
        checkpoint_content: str | None = None
        if checkpoint:
            checkpoint_id, checkpoint_path, checkpoint_content = (
                self.checkpoints.prepare(snapshot)
            )
        record = SimulationLogRecord(
            checkpoint_id=checkpoint_id,
            state_hash=snapshot.state_hash,
            result=result.model_copy(update={"checkpoint_id": checkpoint_id}),
            trace=trace,
        )
        log_path, log_content = self.logs.prepare(record)
        persisted = snapshot.model_copy(
            update={"checkpoint_id": checkpoint_id or snapshot.checkpoint_id}
        )
        manifest = SessionManifest.from_snapshot(persisted)
        session_path, session_content = self.sessions.prepare(manifest)
        observations = self.logs.prepare_observations(snapshot, step=result.step)

        batch = AtomicBatch(self.root)
        written: list[Path] = []
        if checkpoint_path is not None and checkpoint_content is not None:
            if checkpoint_path.exists():
                if checkpoint_path.read_text(encoding="utf-8") != checkpoint_content:
                    raise ValueError("checkpoint content hash collision")
            else:
                batch.add(
                    self._relative(checkpoint_path),
                    checkpoint_content,
                    overwrite=False,
                )
                written.append(checkpoint_path)
        batch.add(self._relative(log_path), log_content, overwrite=False)
        written.append(log_path)
        for observation_path, observation_content in observations:
            batch.add(
                self._relative(observation_path),
                observation_content,
                overwrite=False,
            )
            written.append(observation_path)
        batch.add(self._relative(session_path), session_content)
        written.append(session_path)

        updated = branch
        if checkpoint_id is not None:
            updated, branch_path, branch_content = self.branches.prepare_advance(
                snapshot.branch_id,
                checkpoint_id=checkpoint_id,
                step=snapshot.current_step,
                expected_head_checkpoint_id=branch.head_checkpoint_id,
            )
            batch.add(self._relative(branch_path), branch_content)
            written.append(branch_path)

        batch.commit(
            precondition=lambda: self.branches.assert_head(
                snapshot.branch_id,
                branch.head_checkpoint_id,
            )
        )
        if checkpoint_id is None or checkpoint_path is None:
            return None
        return CommitResult(
            branch=updated,
            checkpoint_id=checkpoint_id,
            session_id=snapshot.session_id,
            step=snapshot.current_step,
            state_hash=snapshot.state_hash,
            written_paths=tuple(str(path) for path in written),
        )

    def _relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

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
