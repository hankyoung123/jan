from pathlib import Path

from story_engine.domain.memory import MemoryRecord
from story_engine.domain.session_manifest import SessionManifest
from story_engine.domain.simulation import (
    BranchManifest,
    CommitResult,
    StepResult,
    TurnSessionSnapshot,
)
from story_engine.domain.trace import ModelCallStatus, TurnTrace
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.command_store import (
    CommandReceiptCommit,
    CommandReceiptStore,
)
from story_engine.persistence.session_store import SessionStore
from story_engine.persistence.simulation_log import (
    SimulationLogRecord,
    SimulationLogStore,
)
from story_engine.simulation.session import calculate_snapshot_state_hash
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
        self.receipts = CommandReceiptStore(root)

    @staticmethod
    def _history_head(snapshot: TurnSessionSnapshot) -> str:
        if snapshot.history_head_id is None:
            raise ValueError("checkpoint has no simulation history head")
        return snapshot.history_head_id

    @staticmethod
    def _with_history_head(
        snapshot: TurnSessionSnapshot,
        history_head_id: str,
    ) -> TurnSessionSnapshot:
        provisional = snapshot.model_copy(
            update={
                "history_head_id": history_head_id,
                "state_hash": "0" * 64,
            }
        )
        return provisional.model_copy(
            update={"state_hash": calculate_snapshot_state_hash(provisional)}
        )

    @staticmethod
    def _genesis_record(
        snapshot: TurnSessionSnapshot,
        memory_delta: tuple[MemoryRecord, ...],
    ) -> SimulationLogRecord:
        result = StepResult(
            session_id=snapshot.session_id,
            branch_id=snapshot.branch_id,
            step=0,
            acting_actor_id=None,
            action_spec=None,
            action_text=None,
            resolved_turn=None,
            status=snapshot.status,
        )
        trace = TurnTrace(
            trace_id=f"trace:genesis:{snapshot.session_id}",
            session_id=snapshot.session_id,
            branch_id=snapshot.branch_id,
            step=0,
            content_locale=snapshot.content_locale,
            stages=(),
            model_calls=(),
            started_at=snapshot.started_at,
            completed_at=snapshot.started_at,
            status=ModelCallStatus.SUCCEEDED,
        )
        return SimulationLogRecord.create(
            record_kind="genesis",
            parent_log_id=None,
            result=result,
            trace=trace,
            memory_delta=memory_delta,
        )

    def save_checkpoint(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        reason: str,
        genesis_memory_delta: tuple[MemoryRecord, ...] = (),
    ) -> CommitResult:
        del reason
        branch = self.branches.ensure(
            branch_id=snapshot.branch_id,
            project_id=snapshot.project_id,
            content_locale=snapshot.content_locale,
        )
        if (
            branch.head_checkpoint_id is not None
            and snapshot.checkpoint_id == branch.head_checkpoint_id
        ):
            current = self.checkpoints.load(branch.head_checkpoint_id)
            excluded = {"checkpoint_id", "state_hash", "updated_at"}
            if current.model_dump(exclude=excluded) == snapshot.model_dump(
                exclude=excluded
            ):
                return CommitResult(
                    branch=branch,
                    checkpoint_id=branch.head_checkpoint_id,
                    history_head_id=self._history_head(current),
                    session_id=snapshot.session_id,
                    step=current.current_step,
                    state_hash=current.state_hash,
                    written_paths=(),
                )
        genesis_record: SimulationLogRecord | None = None
        persisted_snapshot = snapshot
        if snapshot.history_head_id is None:
            if branch.head_checkpoint_id is not None:
                raise ValueError("checkpoint is missing its simulation history head")
            genesis_record = self._genesis_record(snapshot, genesis_memory_delta)
            persisted_snapshot = self._with_history_head(
                snapshot, genesis_record.log_id
            )
        elif genesis_memory_delta:
            raise ValueError("Genesis memory can only be committed once")

        checkpoint_id, checkpoint_path, checkpoint_content = self.checkpoints.prepare(
            persisted_snapshot,
            parent_checkpoint_id=branch.head_checkpoint_id,
        )
        if genesis_record is not None:
            genesis_record = genesis_record.with_checkpoint(checkpoint_id)
        updated, branch_path, branch_content = self.branches.prepare_advance(
            snapshot.branch_id,
            checkpoint_id=checkpoint_id,
            step=snapshot.current_step,
            expected_head_checkpoint_id=branch.head_checkpoint_id,
        )
        persisted = persisted_snapshot.model_copy(
            update={"checkpoint_id": checkpoint_id}
        )
        manifest = SessionManifest.from_snapshot(persisted)
        session_path, session_content = self.sessions.prepare(manifest)
        batch = AtomicBatch(self.root)
        written: list[Path] = []
        if genesis_record is not None:
            genesis_path, genesis_content = self.logs.prepare(genesis_record)
            batch.add(self._relative(genesis_path), genesis_content, overwrite=False)
            written.append(genesis_path)
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
            history_head_id=self._history_head(persisted),
            session_id=snapshot.session_id,
            step=snapshot.current_step,
            state_hash=persisted.state_hash,
            written_paths=tuple(str(path) for path in written),
        )

    def append_step(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
        trace: TurnTrace,
        *,
        memory_delta: tuple[MemoryRecord, ...] = (),
        checkpoint: bool = True,
        command_receipt: CommandReceiptCommit | None = None,
    ) -> CommitResult | None:
        branch = self.branches.ensure(
            branch_id=snapshot.branch_id,
            project_id=snapshot.project_id,
            content_locale=snapshot.content_locale,
        )
        if snapshot.history_head_id is None:
            raise ValueError("turn snapshot has no simulation history head")
        record = SimulationLogRecord.create(
            record_kind="turn",
            parent_log_id=snapshot.history_head_id,
            result=result,
            trace=trace,
            memory_delta=memory_delta,
        )
        persisted_snapshot = snapshot
        checkpoint_id: str | None = None
        checkpoint_path: Path | None = None
        checkpoint_content: str | None = None
        if checkpoint:
            persisted_snapshot = self._with_history_head(snapshot, record.log_id)
            checkpoint_id, checkpoint_path, checkpoint_content = (
                self.checkpoints.prepare(
                    persisted_snapshot,
                    parent_checkpoint_id=branch.head_checkpoint_id,
                )
            )
            record = record.with_checkpoint(checkpoint_id)
        log_path, log_content = self.logs.prepare(record)
        persisted = persisted_snapshot.model_copy(
            update={"checkpoint_id": checkpoint_id or snapshot.checkpoint_id}
        )
        receipt_path: Path | None = None
        receipt_content: str | None = None
        if command_receipt is not None:
            receipt_result = result.model_copy(
                update={"checkpoint_id": checkpoint_id}
            )
            receipt_path, receipt_content = self.receipts.prepare(
                session_id=snapshot.session_id,
                command_id=command_receipt.command_id,
                operation=command_receipt.operation,
                expected_state_hash=command_receipt.expected_state_hash,
                request_fingerprint=command_receipt.request_fingerprint,
                result_type=type(receipt_result).__name__,
                result=receipt_result.model_dump(mode="json"),
                committed_checkpoint_id=checkpoint_id,
            )
        manifest = SessionManifest.from_snapshot(persisted)
        session_path, session_content = self.sessions.prepare(manifest)
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
        batch.add(self._relative(session_path), session_content)
        written.append(session_path)
        if receipt_path is not None and receipt_content is not None:
            batch.add(
                self._relative(receipt_path),
                receipt_content,
                overwrite=False,
            )
            written.append(receipt_path)

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

        def assert_commit_preconditions() -> None:
            self.branches.assert_head(
                snapshot.branch_id,
                branch.head_checkpoint_id,
            )

        batch.commit(precondition=assert_commit_preconditions)
        if checkpoint_id is None or checkpoint_path is None:
            return None
        return CommitResult(
            branch=updated,
            checkpoint_id=checkpoint_id,
            history_head_id=self._history_head(persisted),
            session_id=snapshot.session_id,
            step=snapshot.current_step,
            state_hash=persisted.state_hash,
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
