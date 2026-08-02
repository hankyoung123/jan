import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from story_engine.domain.simulation import BranchManifest
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.lock import ProjectLock

_BRANCH_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,127}$")


class BranchConflictError(RuntimeError):
    """Raised when a branch head changed since the caller read it."""


class BranchStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / ".story-engine/runtime/branches"
        self.checkpoints = CheckpointStore(root)

    def _path(self, branch_id: str) -> Path:
        if not _BRANCH_ID.fullmatch(branch_id):
            raise ValueError("invalid branch ID")
        return self.directory / f"{branch_id}.json"

    def path_for(self, branch_id: str) -> Path:
        return self._path(branch_id)

    @staticmethod
    def _content(manifest: BranchManifest) -> str:
        return (
            json.dumps(
                manifest.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    def load(self, branch_id: str) -> BranchManifest:
        try:
            payload: Any = json.loads(self._path(branch_id).read_text(encoding="utf-8"))
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
        if not self.checkpoints.exists(checkpoint_id):
            raise ValueError("cannot advance branch to a missing checkpoint")
        with ProjectLock(self.root):
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
            atomic_write_text(self._path(branch_id), self._content(updated))
            return updated

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
