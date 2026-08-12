import uuid
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
from story_engine.domain.wiki import WikiPatch, WikiPatchOperation
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.persistence.projection_store import ProjectionTaskStore
from story_engine.simulation.projections import ProjectionTaskService
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.wiki.store import WikiStore

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

    async def rebuild(self, *_args, **_kwargs) -> None:
        await self.process()


class HistoricalOverwriteDetectingProcessor:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.processed_checkpoint_id: str | None = None
        self.rebuilt_checkpoint_id: str | None = None

    async def process(self, snapshot, **_kwargs) -> None:
        self.processed_checkpoint_id = snapshot.checkpoint_id
        store = WikiStore(self.root, snapshot.branch_id)
        page = store.load_page("world/state.md")
        store.apply_patches(
            (
                WikiPatch(
                    path=page.path,
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content="## Invalid retry\n\nFuture state rewrote the past.",
                    source_ids=("project:fog-harbor",),
                    expected_revision=page.revision,
                    expected_content_hash=page.content_hash,
                ),
            ),
            checkpoint_id=snapshot.checkpoint_id,
            step=snapshot.current_step,
        )

    async def rebuild(self, snapshot) -> None:
        self.rebuilt_checkpoint_id = snapshot.checkpoint_id


def _version_bytes(store: WikiStore, checkpoint_id: str) -> dict[str, bytes]:
    root = store.version_root(checkpoint_id)
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*.md"))
    }


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


def test_retrying_old_projection_preserves_historical_wiki_versions(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    root = tmp_path / "fog-harbor"
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
        store = WikiStore(root, "main")
        first_page = store.load_page("world/state.md")
        store.apply_patches(
            (
                WikiPatch(
                    path=first_page.path,
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content="## Wiki 40\n\nFirst immutable projection.",
                    source_ids=("project:fog-harbor",),
                    expected_revision=first_page.revision,
                    expected_content_hash=first_page.content_hash,
                ),
            ),
            checkpoint_id=started["checkpoint_id"],
            step=40,
        )
        stepped = client.post(
            f"/projects/fog-harbor/simulations/{started['session_id']}/step",
            headers=AUTH,
            json={
                "command_id": f"command:{uuid.uuid4().hex}",
                "expected_state_hash": started["state_hash"],
            },
        ).json()

    second_page = store.load_page("world/state.md")
    store.apply_patches(
        (
            WikiPatch(
                path=second_page.path,
                operation=WikiPatchOperation.APPEND_HISTORY,
                content="## Wiki 80\n\nFuture projection remains separate.",
                source_ids=("project:fog-harbor",),
                expected_revision=second_page.revision,
                expected_content_hash=second_page.content_hash,
            ),
        ),
        checkpoint_id=stepped["checkpoint_id"],
        step=80,
    )
    before_first = _version_bytes(store, started["checkpoint_id"])
    before_second = _version_bytes(store, stepped["checkpoint_id"])
    first_snapshot = CheckpointStore(root).load(started["checkpoint_id"])
    task = ProjectionTask(
        task_id="projection:wiki:historical-immutability",
        project_id=first_snapshot.project_id,
        session_id=first_snapshot.session_id,
        branch_id=first_snapshot.branch_id,
        checkpoint_id=started["checkpoint_id"],
        step=40,
        boundary=SimulationBoundary.SCENE,
        kind=ProjectionKind.WIKI,
        status=ProjectionTaskStatus.FAILED,
        error_text="retry this old task",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )
    tasks = ProjectionTaskStore(root)
    tasks.create(task)
    processor = HistoricalOverwriteDetectingProcessor(root)
    service = ProjectionTaskService(
        root,
        wiki_processor=processor,  # type: ignore[arg-type]
        manuscript_agent=object(),  # type: ignore[arg-type]
    )

    service.retry(session_id=task.session_id, task_id=task.task_id)
    _wait_for_status(tasks, task.task_id, ProjectionTaskStatus.SUCCEEDED)
    service.shutdown()

    assert processor.processed_checkpoint_id is None
    assert processor.rebuilt_checkpoint_id == stepped["checkpoint_id"]
    assert _version_bytes(store, started["checkpoint_id"]) == before_first
    assert _version_bytes(store, stepped["checkpoint_id"]) == before_second


def test_projection_skips_checkpoint_abandoned_before_execution(
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
        stepped = client.post(
            f"/projects/fog-harbor/simulations/{started['session_id']}/step",
            headers=AUTH,
            json={
                "command_id": f"command:{uuid.uuid4().hex}",
                "expected_state_hash": started["state_hash"],
            },
        ).json()

    root = tmp_path / "fog-harbor"
    SimulationCommitKernel(root).rollback_branch(
        "fog-harbor",
        "main",
        checkpoint_id=started["checkpoint_id"],
    )
    abandoned = CheckpointStore(root).load(stepped["checkpoint_id"])
    task = ProjectionTask(
        task_id="projection:wiki:abandoned-checkpoint",
        project_id=abandoned.project_id,
        session_id=abandoned.session_id,
        branch_id=abandoned.branch_id,
        checkpoint_id=stepped["checkpoint_id"],
        step=abandoned.current_step,
        boundary=SimulationBoundary.SCENE,
        kind=ProjectionKind.WIKI,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    store = ProjectionTaskStore(root)
    store.create(task)
    processor = BlockingWikiProcessor()
    service = ProjectionTaskService(
        root,
        wiki_processor=processor,  # type: ignore[arg-type]
        manuscript_agent=object(),  # type: ignore[arg-type]
    )

    skipped = _wait_for_status(
        store,
        task.task_id,
        ProjectionTaskStatus.SKIPPED,
    )
    service.shutdown()

    assert skipped.attempt_count == 0
    assert "no longer reachable" in (skipped.error_text or "")
    assert not processor.entered.is_set()
