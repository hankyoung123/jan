import asyncio
import time
from collections import defaultdict, deque
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock, Thread

from story_engine.domain.projection import (
    ProjectionKind,
    ProjectionTask,
    ProjectionTaskStatus,
    SimulationBoundary,
)
from story_engine.domain.simulation import (
    ManuscriptGenerationMode,
    StepResult,
    TurnSessionSnapshot,
    WikiMaintenanceMode,
)
from story_engine.manuscript.models import (
    ManuscriptGenerationRequest,
    WriterSourceSelection,
)
from story_engine.manuscript.service import ManuscriptAgent, ManuscriptService
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.projection_store import ProjectionTaskStore
from story_engine.persistence.simulation_log import SimulationLogStore
from story_engine.wiki.boundary import WikiBoundaryProcessor
from story_engine.wiki.consolidator import WikiProtocolError
from story_engine.wiki.store import WikiStore


class ProjectionTaskService:
    """Queue rebuildable Wiki and manuscript views after core commits succeed."""

    def __init__(
        self,
        root: Path,
        *,
        wiki_processor: WikiBoundaryProcessor,
        manuscript_agent: ManuscriptAgent,
    ) -> None:
        self.root = root
        self.store = ProjectionTaskStore(root)
        self.checkpoints = CheckpointStore(root)
        self.logs = SimulationLogStore(root)
        self.wiki_processor = wiki_processor
        self.manuscript_agent = manuscript_agent
        self._lock = RLock()
        self._queues: dict[str, deque[str]] = defaultdict(deque)
        self._queued: set[str] = set()
        self._workers: dict[str, Thread] = {}
        self._active_tasks: dict[str, str] = {}
        self._shutting_down = False
        self._recover_persisted_tasks()

    def _recover_persisted_tasks(self) -> None:
        """Requeue durable work after a process exits during a projection."""
        project_id = self.root.name
        with self._lock:
            tasks = self.store.list(project_id=project_id)
            recoverable: list[ProjectionTask] = []
            for task in tasks:
                if task.status == ProjectionTaskStatus.RUNNING:
                    task = self.store.transition(
                        task.task_id,
                        status=ProjectionTaskStatus.PENDING,
                        error_text=None,
                    )
                if task.status == ProjectionTaskStatus.PENDING:
                    recoverable.append(task)
            self._schedule_locked(
                sorted(
                    recoverable,
                    key=lambda item: (
                        item.branch_id,
                        item.step,
                        0 if item.kind == ProjectionKind.WIKI else 1,
                        item.task_id,
                    ),
                )
            )

    def enqueue_boundary(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> tuple[ProjectionTask, ...]:
        if result.boundary == SimulationBoundary.NONE:
            return ()
        if snapshot.checkpoint_id is None:
            raise ValueError("projection tasks require a durable checkpoint")
        tasks = tuple(
            self.store.create(task)
            for task in self._planned_tasks(result, snapshot)
        )
        self._schedule(
            task for task in tasks if task.status == ProjectionTaskStatus.PENDING
        )
        return tasks

    def list(
        self,
        *,
        project_id: str,
        session_id: str,
    ) -> tuple[ProjectionTask, ...]:
        return self.store.list(project_id=project_id, session_id=session_id)

    def retry(self, *, session_id: str, task_id: str) -> ProjectionTask:
        with self._lock:
            task = self.store.load(task_id)
            if task.session_id != session_id:
                raise FileNotFoundError(task_id)
            if task.status == ProjectionTaskStatus.SUCCEEDED:
                raise RuntimeError("succeeded projection tasks cannot be retried")
            replay = self._replay_tasks_locked(task)
            return next(item for item in replay if item.task_id == task_id)

    def rebuild(
        self,
        *,
        project_id: str,
        session_id: str,
        checkpoint_id: str,
        kind: ProjectionKind | None = None,
    ) -> tuple[ProjectionTask, ...]:
        """Recreate all persisted projection tasks reachable from a checkpoint.

        Checkpoints and raw turn records are immutable inputs. Existing task
        files are reset to pending and replayed in branch order so a rebuild
        cannot leave an older projection state behind a newer one.
        """
        with self._lock:
            head = self.checkpoints.load(checkpoint_id)
            if head.project_id != project_id:
                raise ValueError("checkpoint belongs to another project")
            records = self.logs.reachable(self.checkpoints, checkpoint_id)
            planned: list[ProjectionTask] = []
            for record in records:
                if record.checkpoint_id is None:
                    continue
                source = self.checkpoints.load(record.checkpoint_id)
                planned.extend(
                    task
                    for task in self._planned_tasks(record.result, source)
                    if kind is None or task.kind == kind
                )
            replay = self._reset_and_schedule_locked(planned)
            return tuple(task for task in replay if task.session_id == session_id)

    @staticmethod
    def _task_order(task: ProjectionTask) -> tuple[int, int, str]:
        return (
            task.step,
            0 if task.kind == ProjectionKind.WIKI else 1,
            task.task_id,
        )

    def _replay_tasks_locked(
        self,
        task: ProjectionTask,
    ) -> tuple[ProjectionTask, ...]:
        if any(
            queued_id != task.task_id
            and self.store.load(queued_id).branch_id == task.branch_id
            for queued_id in self._queued
        ):
            raise RuntimeError("projection branch already has queued work")
        candidates = tuple(
            item
            for item in self.store.list(project_id=task.project_id)
            if item.branch_id == task.branch_id and item.step >= task.step
        )
        if not candidates:
            candidates = (task,)
        return self._reset_and_schedule_locked(candidates)

    def _reset_and_schedule_locked(
        self,
        candidates: Iterable[ProjectionTask],
    ) -> tuple[ProjectionTask, ...]:
        planned_by_id: dict[str, ProjectionTask] = {
            task.task_id: task for task in candidates
        }
        ordered: list[ProjectionTask] = []
        for task in sorted(planned_by_id.values(), key=self._task_order):
            if task.task_id in self._queued:
                raise RuntimeError("projection task is already running")
            existing = self.store.create(task)
            if existing.status == ProjectionTaskStatus.RUNNING:
                raise RuntimeError("projection task is already running")
            pending = self.store.transition(
                existing.task_id,
                status=ProjectionTaskStatus.PENDING,
            )
            ordered.append(pending)
        self._schedule_locked(ordered)
        return tuple(ordered)

    def _planned_tasks(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> tuple[ProjectionTask, ...]:
        kinds: list[ProjectionKind] = []
        if self._should_update_wiki(snapshot, result.boundary):
            kinds.append(ProjectionKind.WIKI)
        if self._should_write_manuscript(snapshot, result.boundary):
            kinds.append(ProjectionKind.MANUSCRIPT)
        assert snapshot.checkpoint_id is not None
        now = datetime.now(UTC)
        return tuple(
            ProjectionTask(
                task_id=f"projection:{kind.value}:{snapshot.checkpoint_id}",
                project_id=snapshot.project_id,
                session_id=snapshot.session_id,
                branch_id=snapshot.branch_id,
                checkpoint_id=snapshot.checkpoint_id,
                step=result.step,
                boundary=result.boundary,
                kind=kind,
                created_at=now,
                updated_at=now,
            )
            for kind in kinds
        )

    @staticmethod
    def _should_update_wiki(
        snapshot: TurnSessionSnapshot,
        boundary: SimulationBoundary,
    ) -> bool:
        mode = snapshot.request.output.wiki_mode
        return mode == WikiMaintenanceMode.AFTER_SCENE or (
            mode == WikiMaintenanceMode.AFTER_CHAPTER
            and boundary == SimulationBoundary.CHAPTER
        )

    @staticmethod
    def _should_write_manuscript(
        snapshot: TurnSessionSnapshot,
        boundary: SimulationBoundary,
    ) -> bool:
        mode = snapshot.request.output.manuscript_mode
        return mode == ManuscriptGenerationMode.AFTER_SCENE or (
            mode == ManuscriptGenerationMode.AFTER_CHAPTER
            and boundary == SimulationBoundary.CHAPTER
        )

    def _schedule(self, tasks: Iterable[ProjectionTask]) -> None:
        with self._lock:
            self._schedule_locked(tasks)

    def _schedule_locked(self, tasks: Iterable[ProjectionTask]) -> None:
        if self._shutting_down:
            return
        for task in tasks:
            if task.task_id in self._queued:
                continue
            self._queued.add(task.task_id)
            self._queues[task.branch_id].append(task.task_id)
            worker = self._workers.get(task.branch_id)
            if worker is None or not worker.is_alive():
                worker = Thread(
                    target=self._run_branch_queue,
                    args=(task.branch_id,),
                    name=f"projection-{task.branch_id}",
                    daemon=True,
                )
                self._workers[task.branch_id] = worker
                worker.start()

    def _run_branch_queue(self, branch_id: str) -> None:
        while True:
            with self._lock:
                queue = self._queues[branch_id]
                if self._shutting_down or not queue:
                    self._workers.pop(branch_id, None)
                    return
                task_id = queue.popleft()
                self._active_tasks[branch_id] = task_id
            try:
                self._execute(task_id)
            except Exception:
                # A task status write can fail independently of the task itself.
                # Keep consuming this branch queue so one broken projection never
                # strands later Wiki or manuscript work.
                pass
            finally:
                with self._lock:
                    self._active_tasks.pop(branch_id, None)
                    self._queued.discard(task_id)

    def shutdown(self, *, timeout_seconds: float = 5.0) -> None:
        """Stop accepting work and durably mark tasks that outlive shutdown."""
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            workers = tuple(self._workers.values())
        deadline = time.monotonic() + max(0.0, timeout_seconds)
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            active = tuple(self._active_tasks.values())
            queued = tuple(
                task_id for queue in self._queues.values() for task_id in queue
            )
            for queue in self._queues.values():
                queue.clear()
            self._queued.difference_update(queued)
        for task_id in active:
            try:
                task = self.store.load(task_id)
                if task.status == ProjectionTaskStatus.RUNNING:
                    self.store.transition(
                        task_id,
                        status=ProjectionTaskStatus.FAILED,
                        error_text="interrupted by process shutdown",
                    )
            except (FileNotFoundError, OSError, ValueError):
                continue

    def _execute(self, task_id: str) -> None:
        task = self.store.transition(
            task_id,
            status=ProjectionTaskStatus.RUNNING,
            increment_attempt=True,
        )
        try:
            snapshot = self.checkpoints.load(task.checkpoint_id)
            if (
                snapshot.project_id != task.project_id
                or snapshot.session_id != task.session_id
                or snapshot.branch_id != task.branch_id
            ):
                raise ValueError("projection task checkpoint does not match its source")
            if task.kind == ProjectionKind.WIKI:
                self._project_wiki(task, snapshot)
            else:
                self._project_manuscript(task, snapshot)
        except Exception as error:
            try:
                current = self.store.load(task_id)
                if current.status == ProjectionTaskStatus.RUNNING:
                    self.store.transition(
                        task_id,
                        status=ProjectionTaskStatus.FAILED,
                        error_text=str(error),
                    )
            except (FileNotFoundError, OSError, ValueError):
                pass
        else:
            current = self.store.load(task_id)
            if current.status == ProjectionTaskStatus.RUNNING:
                self.store.transition(
                    task_id,
                    status=ProjectionTaskStatus.SUCCEEDED,
                )

    def _project_wiki(
        self,
        task: ProjectionTask,
        snapshot: TurnSessionSnapshot,
    ) -> None:
        try:
            asyncio.run(
                self.wiki_processor.process(
                    snapshot,
                    boundary=task.boundary,
                    end_step=task.step,
                )
            )
        except WikiProtocolError as error:
            detail = str(error)
            WikiStore(self.root, task.branch_id).mark_degraded(
                task.checkpoint_id,
                task.step,
                detail,
            )
            raise RuntimeError(f"wiki degraded: {detail}") from error
        except Exception:
            WikiStore(self.root, task.branch_id).mark_stale(
                task.checkpoint_id,
                task.step,
            )
            raise

    def _project_manuscript(
        self,
        task: ProjectionTask,
        snapshot: TurnSessionSnapshot,
    ) -> None:
        service = ManuscriptService(
            self.root,
            task.branch_id,
            agent=self.manuscript_agent,
        )
        asyncio.run(
            service.generate(
                ManuscriptGenerationRequest(
                    source=WriterSourceSelection(),
                    chapter_id=f"chapter-{max(1, snapshot.completed_scenes):03d}",
                    viewpoint_actor_id=None,
                )
            )
        )
