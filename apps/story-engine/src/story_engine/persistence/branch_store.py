import re
from datetime import UTC, datetime
from pathlib import Path

from story_engine.domain.simulation import BranchManifest
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope
from story_engine.workspace.lock import ProjectLock

_BRANCH_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,127}$")


class BranchConflictError(RuntimeError):
    """Raised when a branch head changed since the caller read it."""


class CheckpointNotReachableError(BranchConflictError):
    """Raised when a derived view targets abandoned branch history."""


class BranchStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / ".story-engine/runtime/branches"
        self.checkpoints = CheckpointStore(root)

    def _path(self, branch_id: str) -> Path:
        if not _BRANCH_ID.fullmatch(branch_id):
            raise ValueError("invalid branch ID")
        return self.directory / f"{branch_id}.md"

    def path_for(self, branch_id: str) -> Path:
        return self._path(branch_id)

    @staticmethod
    def _content(manifest: BranchManifest) -> str:
        return dump_json_envelope(
            schema="story-engine/branch/v1",
            title=f"Branch {manifest.branch_id}",
            metadata={
                "branch_id": manifest.branch_id,
                "step": manifest.head_step,
                "head_checkpoint_id": manifest.head_checkpoint_id,
            },
            payload=manifest.model_dump(mode="json"),
        )

    def load(self, branch_id: str) -> BranchManifest:
        try:
            payload = load_json_envelope(
                self._path(branch_id),
                schema="story-engine/branch/v1",
            )
            manifest = BranchManifest.model_validate(payload)
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as error:
            raise ValueError(f"branch {branch_id!r} is invalid") from error
        if manifest.branch_id != branch_id:
            raise ValueError("branch manifest ID mismatch")
        if manifest.head_checkpoint_id is not None and not self.checkpoints.exists(
            manifest.head_checkpoint_id
        ):
            raise ValueError("branch head points to a missing checkpoint")
        return manifest

    def list(self) -> tuple[BranchManifest, ...]:
        if not self.directory.exists():
            return ()
        return tuple(
            sorted(
                (self.load(path.stem) for path in self.directory.glob("*.md")),
                key=lambda branch: (branch.created_at, branch.branch_id),
            )
        )

    def ensure(
        self,
        *,
        branch_id: str,
        project_id: str,
        content_locale: str,
    ) -> BranchManifest:
        with ProjectLock(self.root):
            path = self._path(branch_id)
            if path.exists():
                manifest = self.load(branch_id)
                if manifest.project_id != project_id:
                    raise ValueError("branch belongs to another project")
                return manifest
            now = datetime.now(UTC)
            manifest = BranchManifest(
                branch_id=branch_id,
                project_id=project_id,
                content_locale=content_locale,
                created_at=now,
                updated_at=now,
            )
            atomic_write_text(path, self._content(manifest), overwrite=False)
            return manifest

    def advance(
        self,
        branch_id: str,
        *,
        checkpoint_id: str,
        step: int,
        expected_head_checkpoint_id: str | None,
    ) -> BranchManifest:
        with ProjectLock(self.root):
            if not self.checkpoints.exists(checkpoint_id):
                raise ValueError("cannot advance branch to a missing checkpoint")
            updated, path, content = self.prepare_advance(
                branch_id,
                checkpoint_id=checkpoint_id,
                step=step,
                expected_head_checkpoint_id=expected_head_checkpoint_id,
            )
            atomic_write_text(path, content)
            return updated

    def assert_head(
        self,
        branch_id: str,
        expected_head_checkpoint_id: str | None,
    ) -> None:
        if self.load(branch_id).head_checkpoint_id != expected_head_checkpoint_id:
            raise BranchConflictError("branch head changed concurrently")

    def checkpoint_is_reachable(self, branch_id: str, checkpoint_id: str) -> bool:
        head_checkpoint_id = self.load(branch_id).head_checkpoint_id
        if head_checkpoint_id is None:
            return False
        return checkpoint_id in self.checkpoints.lineage(head_checkpoint_id)

    def assert_checkpoint_reachable(
        self,
        branch_id: str,
        checkpoint_id: str,
    ) -> None:
        if not self.checkpoint_is_reachable(branch_id, checkpoint_id):
            raise CheckpointNotReachableError(
                "projection checkpoint is no longer reachable from branch head"
            )

    def prepare_advance(
        self,
        branch_id: str,
        *,
        checkpoint_id: str,
        step: int,
        expected_head_checkpoint_id: str | None,
    ) -> tuple[BranchManifest, Path, str]:
        current = self.load(branch_id)
        if current.head_checkpoint_id != expected_head_checkpoint_id:
            raise BranchConflictError("branch head changed concurrently")
        updated = current.model_copy(
            update={
                "head_checkpoint_id": checkpoint_id,
                "head_step": step,
                "updated_at": datetime.now(UTC),
            }
        )
        return updated, self._path(branch_id), self._content(updated)

    def create(
        self,
        *,
        branch_id: str,
        project_id: str,
        source_checkpoint_id: str,
        parent_branch_id: str,
        content_locale: str,
    ) -> BranchManifest:
        if not self.checkpoints.exists(source_checkpoint_id):
            raise ValueError("fork checkpoint does not exist")
        parent = self.load(parent_branch_id)
        if parent.project_id != project_id:
            raise ValueError("parent branch belongs to another project")
        snapshot = self.checkpoints.load(source_checkpoint_id)
        if snapshot.project_id != project_id:
            raise ValueError("fork checkpoint belongs to another project")
        now = datetime.now(UTC)
        manifest = BranchManifest(
            branch_id=branch_id,
            project_id=project_id,
            parent_branch_id=parent_branch_id,
            fork_checkpoint_id=source_checkpoint_id,
            head_checkpoint_id=source_checkpoint_id,
            head_step=snapshot.current_step,
            content_locale=content_locale,
            created_at=now,
            updated_at=now,
        )
        with ProjectLock(self.root):
            atomic_write_text(
                self._path(branch_id),
                self._content(manifest),
                overwrite=False,
            )
        return manifest

    def rollback(self, branch_id: str, checkpoint_id: str) -> BranchManifest:
        if not self.checkpoints.exists(checkpoint_id):
            raise ValueError("rollback checkpoint does not exist")
        snapshot = self.checkpoints.load(checkpoint_id)
        with ProjectLock(self.root):
            current = self.load(branch_id)
            if snapshot.project_id != current.project_id:
                raise ValueError("rollback checkpoint belongs to another project")
            updated = current.model_copy(
                update={
                    "head_checkpoint_id": checkpoint_id,
                    "head_step": snapshot.current_step,
                    "updated_at": datetime.now(UTC),
                }
            )
            atomic_write_text(self._path(branch_id), self._content(updated))
            return updated
