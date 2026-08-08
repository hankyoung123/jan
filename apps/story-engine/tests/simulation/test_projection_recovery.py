from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from time import sleep

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.domain.projection import (
    ProjectionKind,
    ProjectionTask,
    ProjectionTaskStatus,
    SimulationBoundary,
)
from story_engine.domain.simulation import (
    StepResult,
    TurnSessionRequest,
    TurnSessionStatus,
)
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.projection_store import ProjectionTaskStore
from story_engine.simulation.projections import ProjectionTaskService
from story_engine.submission.service import SubmissionService, fog_harbor_submission

AUTH = {"Authorization": "Bearer projection-token"}


class SnapshotRuntime:
    def __init__(self, session_id: str, request: TurnSessionRequest) -> None:
        self.session_id = session_id
        self.branch_id = request.branch_id
        self.cancellation = Event()

    def execute_step(self, step: int, *, cancellation: Event) -> StepResult:
        del cancellation
        return StepResult(
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            acting_actor_id=None,
            action_spec=None,
            action_text=None,
            resolved_turn=None,
            status=TurnSessionStatus.RUNNING,
        )

    def actor_states(self):
        return {"chen-mo": {}}

    def game_master_states(self):
        return {"story-game-master": {}}

    def memory_snapshots(self):
        return {}

    def restore_states(self, **_kwargs):
        return None

    def set_content_locale(self, _content_locale: str) -> None:
        return None

    def set_observer(self, _observer) -> None:
        return None

    def drain_stage_events(self):
        return ()


class BlockingWikiProcessor:
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()

    async def process(self, *_args, **_kwargs) -> None:
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("test projection was not released")


def _wait_for_status(
    store: ProjectionTaskStore,
    task_id: str,
    status: ProjectionTaskStatus,
) -> ProjectionTask:
    for _ in range(100):
        task = store.load(task_id)
        if task.status == status:
            return task
        sleep(0.01)
    raise AssertionError(f"projection task did not reach {status.value}")


def test_projection_service_recovers_running_tasks_and_marks_shutdown_timeout(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    app = create_app(
        EngineSettings(session_token="projection-token", projects_root=tmp_path),
        simulation_runtime_factory=SnapshotRuntime,  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 2},
            },
        ).json()

    root = tmp_path / "fog-harbor"
    snapshot = CheckpointStore(root).load(started["checkpoint_id"])
    store = ProjectionTaskStore(root)
    first = ProjectionTask(
        task_id="projection:wiki:recovery-test",
        project_id=snapshot.project_id,
        session_id=snapshot.session_id,
        branch_id=snapshot.branch_id,
        checkpoint_id=started["checkpoint_id"],
        step=snapshot.current_step,
        boundary=SimulationBoundary.SCENE,
        kind=ProjectionKind.WIKI,
        status=ProjectionTaskStatus.RUNNING,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    store.create(first)
    processor = BlockingWikiProcessor()
    service = ProjectionTaskService(
        root,
        wiki_processor=processor,  # type: ignore[arg-type]
        manuscript_agent=object(),  # type: ignore[arg-type]
    )

    assert processor.entered.wait(timeout=1)
    running = store.load(first.task_id)
    assert running.status == ProjectionTaskStatus.RUNNING
    assert running.attempt_count == 1
    processor.release.set()
    assert _wait_for_status(store, first.task_id, ProjectionTaskStatus.SUCCEEDED)

    processor.entered.clear()
    processor.release.clear()
    second = first.model_copy(
        update={
            "task_id": "projection:wiki:shutdown-test",
            "step": snapshot.current_step + 1,
            "status": ProjectionTaskStatus.PENDING,
            "attempt_count": 0,
            "completed_at": None,
            "error_text": None,
            "updated_at": datetime.now(UTC),
        }
    )
    store.create(second)
    service.retry(session_id=snapshot.session_id, task_id=second.task_id)
    assert processor.entered.wait(timeout=1)
    service.shutdown(timeout_seconds=0)
    interrupted = store.load(second.task_id)
    assert interrupted.status == ProjectionTaskStatus.FAILED
    assert interrupted.error_text == "interrupted by process shutdown"
    processor.release.set()
    assert _wait_for_status(store, second.task_id, ProjectionTaskStatus.FAILED)


def test_projection_rebuild_ignores_a_completed_workers_stale_queue_marker(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    app = create_app(
        EngineSettings(session_token="projection-token", projects_root=tmp_path),
        simulation_runtime_factory=SnapshotRuntime,  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 2},
            },
        ).json()

    root = tmp_path / "fog-harbor"
    snapshot = CheckpointStore(root).load(started["checkpoint_id"])
    task = ProjectionTask(
        task_id="projection:wiki:completed-worker",
        project_id=snapshot.project_id,
        session_id=snapshot.session_id,
        branch_id=snapshot.branch_id,
        checkpoint_id=started["checkpoint_id"],
        step=snapshot.current_step,
        boundary=SimulationBoundary.SCENE,
        kind=ProjectionKind.WIKI,
        status=ProjectionTaskStatus.SUCCEEDED,
        attempt_count=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    store = ProjectionTaskStore(root)
    store.create(task)
    service = ProjectionTaskService(
        root,
        wiki_processor=BlockingWikiProcessor(),  # type: ignore[arg-type]
        manuscript_agent=object(),  # type: ignore[arg-type]
    )
    service.shutdown()

    with service._lock:
        service._queued.add(task.task_id)
        replay = service._reset_and_schedule_locked((task,))

    assert replay[0].status == ProjectionTaskStatus.PENDING
    assert store.load(task.task_id).status == ProjectionTaskStatus.PENDING
