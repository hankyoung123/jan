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

    def set_content_locale(self, content_locale: str) -> None:
        self.content_locale = content_locale


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
    terminated = client.post(
        f"/projects/fog-harbor/simulations/{session_id}/terminate",
        headers=AUTH,
        json={"reason_text": "用户结束测试"},
    )

    assert stepped.status_code == 200
    assert stepped.json()["status"] == "paused"
    assert resumed.status_code == 200
    assert resumed.json()["current_step"] == 2
    assert resumed.json()["status"] == "paused"
    assert terminated.status_code == 200
    assert terminated.json()["status"] == "terminated"
    assert terminated.json()["termination_reason_text"] == "用户结束测试"
    assert app.state.event_bus.sequence == 9


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

    assert checkpointed.status_code == 200
    assert forked.status_code == 201
    assert forked.json()["parent_branch_id"] == "main"
    assert projected.status_code == 200
    assert len(projected.json()["written_paths"]) == 3
    assert rolled_back.status_code == 200
    assert rolled_back.json()["head_checkpoint_id"] == checkpoint_id


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
    assert "/projects/{project_id}/branches/{branch_id}/rollback" in paths
    assert "/projects/{project_id}/branches/{branch_id}/projection" in paths
