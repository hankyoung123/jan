import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.domain.simulation import (
    StepResult,
    TurnSessionRequest,
    TurnSessionStatus,
)
from story_engine.domain.trace import (
    SimulationStage,
    SimulationStageEvent,
    StageStatus,
)
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.submission.service import SubmissionService, fog_harbor_submission

AUTH = {"Authorization": "Bearer test-token"}


class StubRuntime:
    def __init__(self, session_id: str, request: TurnSessionRequest) -> None:
        self.session_id = session_id
        self.branch_id = request.branch_id
        self.cancellation = Event()

    def execute_step(self, step: int, *, cancellation: Event) -> StepResult:
        if cancellation.is_set() or self.cancellation.is_set():
            raise RuntimeError("cancelled")
        return StepResult(
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            acting_actor_id="chen-mo",
            action_spec=None,
            action_text="检查灯塔装置。",
            resolved_turn=None,
            status=TurnSessionStatus.RUNNING,
        )

    def actor_states(self):
        return {"chen-mo": {}}

    def game_master_states(self):
        return {"story-game-master": {}}

    def memory_snapshots(self):
        return {}

    def restore_states(
        self,
        *,
        actor_states,
        game_master_states,
        memory_snapshots,
    ) -> None:
        del actor_states, game_master_states, memory_snapshots

    def set_content_locale(self, content_locale: str) -> None:
        self.content_locale = content_locale

    def set_observer(self, observer) -> None:
        self.observer = observer

    def drain_stage_events(self):
        return ()


class BlockingRuntime(StubRuntime):
    def __init__(
        self,
        session_id: str,
        request: TurnSessionRequest,
        entered: Event,
        release: Event,
    ) -> None:
        super().__init__(session_id, request)
        self.entered = entered
        self.release = release

    def execute_step(self, step: int, *, cancellation: Event) -> StepResult:
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("test stage did not receive release")
        return super().execute_step(step, cancellation=cancellation)


class FailingRuntime(StubRuntime):
    def __init__(self, session_id: str, request: TurnSessionRequest) -> None:
        super().__init__(session_id, request)
        self.project_id = request.project_id
        self._stage_events: list[SimulationStageEvent] = []

    def execute_step(self, step: int, *, cancellation: Event) -> StepResult:
        del cancellation
        now = datetime.now(UTC)
        event = SimulationStageEvent(
            event_id=f"stage-event:{uuid.uuid4().hex}",
            project_id=self.project_id,
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            stage=SimulationStage.RESOLUTION,
            status=StageStatus.FAILED,
            summary_text="resolver rejected the result",
            error_code="resolver_error",
            started_at=now,
            completed_at=now,
        )
        self._stage_events.append(event)
        self.observer.publish(event)
        raise ValueError("resolver rejected the result")

    def drain_stage_events(self):
        drained = tuple(self._stage_events)
        self._stage_events.clear()
        return drained


def _client(tmp_path: Path) -> tuple[TestClient, object]:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    app = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path),
        simulation_runtime_factory=lambda session_id, request: StubRuntime(
            session_id, request
        ),  # type: ignore[arg-type]
    )
    return TestClient(app), app


def test_simulation_api_start_step_resume_and_terminate(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    started = client.post(
        "/projects/fog-harbor/simulations",
        headers=AUTH,
        json={
            "premise_text": "灯塔突然熄灭。",
            "actor_ids": ["chen-mo"],
            "content_locale": "zh-CN",
            "control": {"mode": "step", "max_steps": 4},
        },
    )

    assert started.status_code == 201
    session_id = started.json()["session_id"]
    assert started.json()["status"] == "created"

    stepped = client.post(
        f"/projects/fog-harbor/simulations/{session_id}/step",
        headers=AUTH,
    )
    resumed = client.post(
        f"/projects/fog-harbor/simulations/{session_id}/resume",
        headers=AUTH,
    )
    for _ in range(100):
        resumed_snapshot = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        )
        if resumed_snapshot.json()["status"] == "paused":
            break
        time.sleep(0.01)
    terminated = client.post(
        f"/projects/fog-harbor/simulations/{session_id}/terminate",
        headers=AUTH,
        json={"reason_text": "用户结束测试"},
    )

    assert stepped.status_code == 200
    assert stepped.json()["status"] == "paused"
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "running"
    assert resumed_snapshot.json()["current_step"] == 2
    assert resumed_snapshot.json()["status"] == "paused"
    assert terminated.status_code == 200
    assert terminated.json()["status"] == "terminated"
    assert terminated.json()["termination_reason_text"] == "用户结束测试"
    assert app.state.event_bus.sequence >= 9


def test_background_run_accepts_pause_while_atomic_step_is_in_flight(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    entered = Event()
    release = Event()
    app = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path),
        simulation_runtime_factory=lambda session_id, request: BlockingRuntime(
            session_id,
            request,
            entered,
            release,
        ),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "灯塔突然熄灭。",
                "actor_ids": ["chen-mo"],
                "content_locale": "zh-CN",
                "control": {
                    "mode": "autonomous",
                    "pause_after_scene": False,
                    "max_steps": 3,
                },
            },
        ).json()
        session_id = started["session_id"]

        accepted = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/run",
            headers=AUTH,
        )
        assert accepted.status_code == 202
        assert entered.wait(timeout=1)
        pause = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/pause",
            headers=AUTH,
        )
        assert pause.status_code == 200
        assert pause.json()["status"] == "running"
        assert pause.json()["pending_control"] == "pause"
        release.set()
        for _ in range(100):
            snapshot = client.get(
                f"/projects/fog-harbor/simulations/{session_id}",
                headers=AUTH,
            ).json()
            if snapshot["status"] == "paused":
                break
            time.sleep(0.01)

        assert snapshot["status"] == "paused"
        assert snapshot["current_step"] == 1


def test_immediate_cancel_discards_in_flight_step_without_failure_event(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    entered = Event()
    release = Event()
    app = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path),
        simulation_runtime_factory=lambda session_id, request: BlockingRuntime(
            session_id,
            request,
            entered,
            release,
        ),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "灯塔突然熄灭。",
                "actor_ids": ["chen-mo"],
                "content_locale": "zh-CN",
                "control": {"mode": "autonomous", "max_steps": 3},
            },
        ).json()
        session_id = started["session_id"]
        assert (
            client.post(
                f"/projects/fog-harbor/simulations/{session_id}/run",
                headers=AUTH,
            ).status_code
            == 202
        )
        assert entered.wait(timeout=1)

        cancelled = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/cancel",
            headers=AUTH,
            json={"reason_text": "立即取消测试"},
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        release.set()

        for _ in range(100):
            events = client.get(
                "/projects/fog-harbor/simulation-events",
                headers=AUTH,
            ).json()
            if any(event["type"] == "simulation.terminated" for event in events):
                break
            time.sleep(0.01)
        snapshot = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()
        trace = client.get(
            "/projects/fog-harbor/branches/main/simulation-trace",
            headers=AUTH,
        ).json()

        assert snapshot["status"] == "cancelled"
        assert snapshot["current_step"] == 0
        assert trace == []
        assert not any(event["type"] == "simulation.failed" for event in events)


def test_app_shutdown_checkpoints_latest_paused_step_before_releasing_runtime(
    tmp_path: Path,
) -> None:
    client, app = _client(tmp_path)
    with client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "灯塔突然熄灭。",
                "actor_ids": ["chen-mo"],
                "content_locale": "zh-CN",
                "control": {
                    "mode": "step",
                    "max_steps": 3,
                    "checkpoint_every_steps": 3,
                },
            },
        ).json()
        stepped = client.post(
            f"/projects/fog-harbor/simulations/{started['session_id']}/step",
            headers=AUTH,
        ).json()
        assert stepped["checkpoint_id"] == started["checkpoint_id"]

    branch = BranchStore(tmp_path / "fog-harbor").load("main")
    assert branch.head_checkpoint_id is not None
    assert branch.head_checkpoint_id != started["checkpoint_id"]
    restored = CheckpointStore(tmp_path / "fog-harbor").load(branch.head_checkpoint_id)
    assert restored.current_step == 1
    assert restored.status == TurnSessionStatus.PAUSED
    assert app.state.simulation_engine.get(started["session_id"]).status == (
        TurnSessionStatus.CANCELLED
    )


def test_failed_stage_is_durable_and_identifies_the_failure_location(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    app = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path),
        simulation_runtime_factory=lambda session_id, request: FailingRuntime(
            session_id,
            request,
        ),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "灯塔突然熄灭。",
                "actor_ids": ["chen-mo"],
                "content_locale": "zh-CN",
                "control": {"mode": "step", "max_steps": 2},
            },
        ).json()
        session_id = started["session_id"]

        failed = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/step",
            headers=AUTH,
        )
        trace = client.get(
            "/projects/fog-harbor/branches/main/simulation-trace",
            headers=AUTH,
        ).json()

        assert failed.status_code == 409
        assert trace[0]["trace"]["status"] == "failed"
        assert trace[0]["trace"]["stages"][0]["stage_type"] == "resolution"
        assert trace[0]["trace"]["stages"][0]["error_code"] == "resolver_error"

        restored = client.post(
            "/projects/fog-harbor/simulations/restore",
            headers=AUTH,
            json={"checkpoint_id": started["checkpoint_id"]},
        )
        assert restored.status_code == 200
        assert restored.json()["session_id"] == session_id
        assert restored.json()["status"] == "paused"


def test_simulation_api_checkpoint_branch_rollback_and_projection(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    started = client.post(
        "/projects/fog-harbor/simulations",
        headers=AUTH,
        json={
            "premise_text": "灯塔突然熄灭。",
            "actor_ids": ["chen-mo"],
            "content_locale": "zh-CN",
            "control": {"mode": "step", "max_steps": 4},
        },
    ).json()
    session_id = started["session_id"]
    checkpoint_id = started["checkpoint_id"]

    checkpointed = client.post(
        f"/projects/fog-harbor/simulations/{session_id}/checkpoint",
        headers=AUTH,
        json={"reason": "fork point"},
    )
    forked = client.post(
        "/projects/fog-harbor/branches",
        headers=AUTH,
        json={
            "branch_id": "alternate",
            "source_checkpoint_id": checkpoint_id,
            "parent_branch_id": "main",
            "content_locale": "zh-CN",
        },
    )
    projected = client.post(
        "/projects/fog-harbor/branches/main/projection",
        headers=AUTH,
        json={"checkpoint_id": checkpoint_id},
    )
    rolled_back = client.post(
        "/projects/fog-harbor/branches/main/rollback",
        headers=AUTH,
        json={"checkpoint_id": checkpoint_id},
    )
    branches = client.get(
        "/projects/fog-harbor/branches",
        headers=AUTH,
    )
    compared = client.get(
        "/projects/fog-harbor/branches/compare?left=main&right=alternate",
        headers=AUTH,
    )

    assert checkpointed.status_code == 200
    assert forked.status_code == 201
    assert forked.json()["parent_branch_id"] == "main"
    assert projected.status_code == 200
    assert len(projected.json()["written_paths"]) == 3
    assert rolled_back.status_code == 200
    assert rolled_back.json()["head_checkpoint_id"] == checkpoint_id
    assert {branch["branch_id"] for branch in branches.json()} == {
        "main",
        "alternate",
    }
    assert compared.status_code == 200
    assert compared.json()["left"]["branch_id"] == "main"


def test_simulation_api_switches_locale_at_step_boundary(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    started = client.post(
        "/projects/fog-harbor/simulations",
        headers=AUTH,
        json={
            "premise_text": "灯塔突然熄灭。",
            "content_locale": "zh-CN",
            "control": {"mode": "step", "max_steps": 2},
        },
    ).json()

    switched = client.post(
        f"/projects/fog-harbor/simulations/{started['session_id']}/locale",
        headers=AUTH,
        json={"content_locale": "en-US"},
    )

    assert switched.status_code == 200
    assert switched.json()["content_locale"] == "en-US"
    assert switched.json()["checkpoint_id"] != started["checkpoint_id"]


def test_checkpoint_interval_logs_every_step_but_advances_head_only_when_due(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    started = client.post(
        "/projects/fog-harbor/simulations",
        headers=AUTH,
        json={
            "premise_text": "灯塔突然熄灭。",
            "actor_ids": ["chen-mo"],
            "content_locale": "zh-CN",
            "control": {
                "mode": "step",
                "max_steps": 4,
                "checkpoint_every_steps": 2,
            },
        },
    ).json()
    session_id = started["session_id"]

    first = client.post(
        f"/projects/fog-harbor/simulations/{session_id}/step",
        headers=AUTH,
    ).json()
    head_after_first = client.get(
        "/projects/fog-harbor/branches/main",
        headers=AUTH,
    ).json()
    second = client.post(
        f"/projects/fog-harbor/simulations/{session_id}/step",
        headers=AUTH,
    ).json()
    trace = client.get(
        "/projects/fog-harbor/branches/main/simulation-trace",
        headers=AUTH,
    ).json()

    assert first["checkpoint_id"] == started["checkpoint_id"]
    assert head_after_first["head_checkpoint_id"] == started["checkpoint_id"]
    assert trace[0]["checkpoint_id"] is None
    assert trace[1]["checkpoint_id"] == second["checkpoint_id"]
    assert second["checkpoint_id"] != started["checkpoint_id"]


def test_simulation_api_rejects_cross_project_or_unknown_session(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)

    missing = client.get(
        "/projects/fog-harbor/simulations/session:missing",
        headers=AUTH,
    )
    invalid_project = client.post(
        "/projects/../simulations",
        headers=AUTH,
        json={
            "premise_text": "test",
            "control": {"mode": "step"},
        },
    )

    assert missing.status_code == 404
    assert invalid_project.status_code == 404


def test_openapi_exposes_session_control_surface(tmp_path: Path) -> None:
    _, app = _client(tmp_path)
    paths = app.openapi()["paths"]

    assert "/projects/{project_id}/simulations" in paths
    assert "/projects/{project_id}/simulations/{session_id}/step" in paths
    assert "/projects/{project_id}/simulations/{session_id}/run" in paths
    assert "/projects/{project_id}/simulations/{session_id}/pause" in paths
    assert "/projects/{project_id}/simulations/{session_id}/resume" in paths
    assert "/projects/{project_id}/simulations/{session_id}/terminate" in paths
    assert "/projects/{project_id}/simulations/{session_id}/locale" in paths
    assert "/projects/{project_id}/simulations/{session_id}/checkpoint" in paths
    assert "/projects/{project_id}/branches" in paths
    assert "/projects/{project_id}/branches/compare" in paths
    assert "/projects/{project_id}/branches/{branch_id}/rollback" in paths
    assert "/projects/{project_id}/branches/{branch_id}/projection" in paths
