import re
from datetime import UTC, datetime
from pathlib import Path

from story_engine.domain.projection import (
    ProjectionTask,
    ProjectionTaskStatus,
)
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope
from story_engine.workspace.lock import ProjectLock

_TASK_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


class ProjectionTaskStore:
    """Durable task state for views derived from immutable Checkpoints."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / ".story-engine/runtime/projections"

    def _path(self, task_id: str) -> Path:
        if not _TASK_ID.fullmatch(task_id):
            raise ValueError("invalid projection task ID")
        return self.directory / f"{task_id.replace(':', '__')}.md"

    @staticmethod
    def _content(task: ProjectionTask) -> str:
        return dump_json_envelope(
            schema="story-engine/projection-task/v1",
            title=f"Projection Task {task.task_id}",
            metadata={
                "task_id": task.task_id,
                "branch_id": task.branch_id,
                "checkpoint_id": task.checkpoint_id,
                "kind": task.kind.value,
                "status": task.status.value,
            },
            payload=task.model_dump(mode="json"),
        )

    def create(self, task: ProjectionTask) -> ProjectionTask:
        path = self._path(task.task_id)
        content = self._content(task)
        with ProjectLock(self.root):
            if path.exists():
                existing = self.load(task.task_id)
                if (
                    existing.project_id != task.project_id
                    or existing.session_id != task.session_id
                    or existing.branch_id != task.branch_id
                    or existing.checkpoint_id != task.checkpoint_id
                    or existing.kind != task.kind
                ):
                    raise ValueError("projection task ID collision")
                return existing
            atomic_write_text(path, content, overwrite=False)
        return task

    def save(self, task: ProjectionTask) -> ProjectionTask:
        path = self._path(task.task_id)
        with ProjectLock(self.root):
            if not path.exists():
                raise FileNotFoundError(task.task_id)
            atomic_write_text(path, self._content(task))
        return task

    def load(self, task_id: str) -> ProjectionTask:
        try:
            payload = load_json_envelope(
                self._path(task_id),
                schema="story-engine/projection-task/v1",
            )
            task = ProjectionTask.model_validate(payload)
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as error:
            raise ValueError(f"projection task {task_id!r} is invalid") from error
        if task.task_id != task_id:
            raise ValueError("projection task ID mismatch")
        return task

    def list(
        self,
        *,
        project_id: str,
        session_id: str | None = None,
    ) -> tuple[ProjectionTask, ...]:
        if not self.directory.exists():
            return ()
        tasks: list[ProjectionTask] = []
        for path in self.directory.glob("*.md"):
            try:
                payload = load_json_envelope(
                    path,
                    schema="story-engine/projection-task/v1",
                )
                task = ProjectionTask.model_validate(payload)
            except (OSError, ValueError) as error:
                raise ValueError(
                    f"projection task {path.name!r} is invalid"
                ) from error
            if task.project_id == project_id and (
                session_id is None or task.session_id == session_id
            ):
                tasks.append(task)
        return tuple(
            sorted(
                tasks,
                key=lambda item: (item.created_at, item.task_id),
            )
        )

    def transition(
        self,
        task_id: str,
        *,
        status: ProjectionTaskStatus,
        error_text: str | None = None,
        increment_attempt: bool = False,
    ) -> ProjectionTask:
        task = self.load(task_id)
        now = datetime.now(UTC)
        updated = task.model_copy(
            update={
                "status": status,
                "attempt_count": task.attempt_count + int(increment_attempt),
                "error_text": error_text,
                "updated_at": now,
                "completed_at": (
                    now
                    if status
                    in {
                        ProjectionTaskStatus.SUCCEEDED,
                        ProjectionTaskStatus.FAILED,
                        ProjectionTaskStatus.SKIPPED,
                    }
                    else None
                ),
            }
        )
        return self.save(updated)
