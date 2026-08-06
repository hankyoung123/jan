import time
import uuid
from concurrent.futures import ThreadPoolExecutor
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
from story_engine.persistence.session_store import SessionStore
from story_engine.submission.service import SubmissionService, fog_harbor_submission

AUTH = {"Authorization": "Bearer test-token"}


def _advance(
    client: TestClient,
    session_id: str,
    operation: str = "step",
    *,
    command_id: str | None = None,
    expected_state_hash: str | None = None,
):
    if expected_state_hash is None:
        expected_state_hash = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()["state_hash"]
    return client.post(
        f"/projects/fog-harbor/simulations/{session_id}/{operation}",
        headers=AUTH,
        json={
            "command_id": command_id or f"command:{uuid.uuid4().hex}",
            "expected_state_hash": expected_state_hash,
        },
    )


class StubRuntime:
    def __init__(self, session_id: str, request: TurnSessionRequest) -> None:
        self.session_id = session_id
        self.branch_id = request.branch_id
        self.cancellation = Event()
        self.execute_count = 0

    def execute_step(self, step: int, *, cancellation: Event) -> StepResult:
        if cancellation.is_set() or self.cancellation.is_set():
            raise RuntimeError("cancelled")
        self.execute_count += 1
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


class CancellationIgnoringBlockingRuntime(BlockingRuntime):
    """Return a normal step after cancellation to exercise the engine boundary."""

    def execute_step(self, step: int, *, cancellation: Event) -> StepResult:
        del cancellation
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise RuntimeError("test stage did not receive release")
        self.execute_count += 1
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

    stepped = _advance(client, session_id)
    resumed = _advance(client, session_id, "resume")
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


def test_direct_terminal_transition_is_restored_from_its_checkpoint(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    with client:
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
        terminated = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/terminate",
            headers=AUTH,
            json={"reason_text": "用户结束测试"},
        ).json()

    reopened = TestClient(
        create_app(
            EngineSettings(session_token="test-token", projects_root=tmp_path),
            simulation_runtime_factory=lambda session_id, request: StubRuntime(
                session_id, request
            ),  # type: ignore[arg-type]
        )
    )
    with reopened:
        restored = reopened.get(
            f"/projects/fog-harbor/simulations/{session_id}", headers=AUTH
        )

    assert restored.status_code == 200
    assert restored.json()["status"] == "terminated"
    assert restored.json()["checkpoint_id"] == terminated["checkpoint_id"]
    assert restored.json()["termination_reason_text"] == "用户结束测试"


def test_two_concurrent_steps_execute_only_one_command(tmp_path: Path) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    entered = Event()
    release = Event()
    runtimes: list[BlockingRuntime] = []

    def runtime_factory(session_id: str, request: TurnSessionRequest):
        runtime = BlockingRuntime(session_id, request, entered, release)
        runtimes.append(runtime)
        return runtime

    app = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path),
        simulation_runtime_factory=runtime_factory,  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
            },
        ).json()
        session_id = started["session_id"]
        expected_hash = started["state_hash"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(
                _advance,
                client,
                session_id,
                command_id="command:first",
                expected_state_hash=expected_hash,
            )
            assert entered.wait(timeout=1)
            second = pool.submit(
                _advance,
                client,
                session_id,
                command_id="command:second",
                expected_state_hash=expected_hash,
            )
            release.set()
            responses = (first.result(timeout=2), second.result(timeout=2))

        assert sorted(response.status_code for response in responses) == [200, 409]
        assert runtimes[0].execute_count == 1
        snapshot = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()
        assert snapshot["current_step"] == 1


def test_repeated_command_id_returns_the_same_step_without_reexecution(
    tmp_path: Path,
) -> None:
    client, app = _client(tmp_path)
    with client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
            },
        ).json()
        session_id = started["session_id"]
        command_id = "command:idempotent-step"

        first = _advance(
            client,
            session_id,
            command_id=command_id,
            expected_state_hash=started["state_hash"],
        )
        repeated = _advance(
            client,
            session_id,
            command_id=command_id,
            expected_state_hash=started["state_hash"],
        )

        assert first.status_code == repeated.status_code == 200
        assert first.json() == repeated.json()
        assert app.state.simulation_service.get(session_id).current_step == 1


def test_command_receipt_survives_sidecar_restart(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
            },
        ).json()
        session_id = started["session_id"]
        command_id = "command:restart-safe"
        first = _advance(
            client,
            session_id,
            command_id=command_id,
            expected_state_hash=started["state_hash"],
        )
        assert first.status_code == 200

    runtimes: list[StubRuntime] = []

    def runtime_factory(session_id: str, request: TurnSessionRequest) -> StubRuntime:
        runtime = StubRuntime(session_id, request)
        runtimes.append(runtime)
        return runtime

    reopened = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path),
        simulation_runtime_factory=runtime_factory,  # type: ignore[arg-type]
    )
    with TestClient(reopened) as client:
        restored = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        )
        repeated = _advance(
            client,
            session_id,
            command_id=command_id,
            expected_state_hash=started["state_hash"],
        )

    assert restored.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert runtimes[0].execute_count == 0


def test_step_rejects_a_stale_expected_state_hash(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    with client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
            },
        ).json()
        session_id = started["session_id"]
        first = _advance(
            client,
            session_id,
            command_id="command:current",
            expected_state_hash=started["state_hash"],
        )

        stale = _advance(
            client,
            session_id,
            command_id="command:stale",
            expected_state_hash=started["state_hash"],
        )

        assert first.status_code == 200
        assert stale.status_code == 409
        assert "state changed" in stale.json()["detail"]


def test_step_cannot_start_during_background_run(tmp_path: Path) -> None:
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
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {
                    "mode": "autonomous",
                    "pause_after_scene": False,
                    "max_steps": 3,
                },
            },
        ).json()
        session_id = started["session_id"]

        accepted = _advance(
            client,
            session_id,
            "run",
            command_id="command:background-run",
            expected_state_hash=started["state_hash"],
        )
        assert accepted.status_code == 202
        assert entered.wait(timeout=1)
        running = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()

        rejected = _advance(
            client,
            session_id,
            command_id="command:overlapping-step",
            expected_state_hash=running["state_hash"],
        )
        assert rejected.status_code == 409
        assert "already running" in rejected.json()["detail"]

        client.post(
            f"/projects/fog-harbor/simulations/{session_id}/pause",
            headers=AUTH,
        )
        release.set()


def test_shutdown_rejects_new_advancing_commands(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    with client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 2},
            },
        ).json()
        app.state.simulation_service.shutdown()
        after_shutdown = client.get(
            f"/projects/fog-harbor/simulations/{started['session_id']}",
            headers=AUTH,
        ).json()

        rejected = _advance(
            client,
            started["session_id"],
            command_id="command:after-shutdown",
            expected_state_hash=after_shutdown["state_hash"],
        )

    assert rejected.status_code == 409
    assert "shutting down" in rejected.json()["detail"]


def test_simulation_start_accepts_a_cast_larger_than_one_scene_roster(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)

    response = client.post(
        "/projects/fog-harbor/simulations",
        headers=AUTH,
        json={
            "premise_text": "The whole cast gathers at the harbor.",
            "actor_ids": [f"agent-{index}" for index in range(5)],
            "content_locale": "en-US",
            "control": {"mode": "step", "max_steps": 4},
        },
    )

    assert response.status_code == 201
    assert response.json()["request"]["actor_ids"] == [
        f"agent-{index}" for index in range(5)
    ]


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

        accepted = _advance(client, session_id, "run")
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
        assert _advance(client, session_id, "run").status_code == 202
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


def test_cancel_discards_a_runtime_result_that_returns_after_cancellation(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    entered = Event()
    release = Event()
    app = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path),
        simulation_runtime_factory=lambda session_id, request: (
            CancellationIgnoringBlockingRuntime(session_id, request, entered, release)
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
        assert _advance(client, session_id, "run").status_code == 202
        assert entered.wait(timeout=1)
        cancelled = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/cancel",
            headers=AUTH,
            json={"reason_text": "取消后仍返回结果"},
        )
        assert cancelled.status_code == 200
        release.set()
        for _ in range(100):
            snapshot = client.get(
                f"/projects/fog-harbor/simulations/{session_id}",
                headers=AUTH,
            ).json()
            if snapshot["status"] == "cancelled":
                break
            time.sleep(0.01)

        branch = BranchStore(tmp_path / "fog-harbor").load("main")
        assert branch.head_checkpoint_id is not None
        durable = CheckpointStore(tmp_path / "fog-harbor").load(
            branch.head_checkpoint_id
        )
        trace = client.get(
            "/projects/fog-harbor/branches/main/simulation-trace",
            headers=AUTH,
        ).json()

    assert snapshot["current_step"] == 0
    assert snapshot["raw_log_offset"] == 0
    assert durable.current_step == 0
    assert durable.raw_log_offset == 0
    assert trace == []


def test_each_successful_step_is_checkpointed_and_shutdown_does_not_duplicate_it(
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
                },
            },
        ).json()
        stepped = _advance(client, started["session_id"]).json()
        assert stepped["checkpoint_id"] != started["checkpoint_id"]

    branch = BranchStore(tmp_path / "fog-harbor").load("main")
    assert branch.head_checkpoint_id is not None
    assert branch.head_checkpoint_id == stepped["checkpoint_id"]
    restored = CheckpointStore(tmp_path / "fog-harbor").load(branch.head_checkpoint_id)
    assert restored.current_step == 1
    assert restored.status == TurnSessionStatus.PAUSED
    assert SessionStore(tmp_path / "fog-harbor").load(started["session_id"]).status == (
        TurnSessionStatus.PAUSED
    )
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

        failed = _advance(client, session_id)
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


def test_background_failure_reopens_from_last_checkpoint(
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
                "control": {
                    "mode": "autonomous",
                    "pause_after_scene": False,
                    "max_steps": 3,
                },
            },
        ).json()
        session_id = started["session_id"]
        accepted = _advance(
            client,
            session_id,
            "run",
            command_id="command:failed-background-run",
            expected_state_hash=started["state_hash"],
        )
        assert accepted.status_code == 202
        current = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()
        for _ in range(100):
            if current["status"] == "failed":
                break
            time.sleep(0.01)
            current = client.get(
                f"/projects/fog-harbor/simulations/{session_id}",
                headers=AUTH,
            ).json()
        assert current["status"] == "failed"
        assert current["current_step"] == 0
        checkpoint_id = started["checkpoint_id"]

    reopened = TestClient(
        create_app(
            EngineSettings(session_token="test-token", projects_root=tmp_path),
            simulation_runtime_factory=lambda session_id, request: StubRuntime(
                session_id,
                request,
            ),  # type: ignore[arg-type]
        )
    )
    with reopened:
        restored = reopened.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        )
        assert restored.status_code == 200
        assert restored.json()["current_step"] == 0
        assert restored.json()["checkpoint_id"] == checkpoint_id
        assert restored.json()["restoration_notice_text"]


def test_abnormal_sidecar_exit_recovers_checkpoint_not_running_manifest(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path)
    with client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
            },
        ).json()
        session_id = started["session_id"]
        stepped = _advance(client, session_id).json()

    checkpoint_id = stepped["checkpoint_id"]
    checkpoint_step = stepped["step"] + 1
    sessions = SessionStore(tmp_path / "fog-harbor")
    manifest = sessions.load(session_id)
    # Simulate a crashed sidecar after it wrote a stale running index but before
    # another completed step could create a new checkpoint.
    sessions.save(
        manifest.model_copy(
            update={
                "status": TurnSessionStatus.RUNNING,
                "current_step": checkpoint_step + 99,
            }
        )
    )

    reopened = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path),
        simulation_runtime_factory=lambda session_id, request: StubRuntime(
            session_id,
            request,
        ),  # type: ignore[arg-type]
    )
    with TestClient(reopened) as client:
        listed = client.get("/projects/fog-harbor/simulations", headers=AUTH)
        restored = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        )

    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "interrupted"
    assert restored.status_code == 200
    assert restored.json()["status"] == "paused"
    assert restored.json()["current_step"] == checkpoint_step
    assert restored.json()["checkpoint_id"] == checkpoint_id
    assert restored.json()["restoration_notice_text"]


def test_list_does_not_persist_an_uncommitted_live_snapshot(
    tmp_path: Path,
) -> None:
    client, app = _client(tmp_path)
    with client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
            },
        ).json()
        session_id = started["session_id"]
        store = SessionStore(tmp_path / "fog-harbor")
        durable_manifest = store.load(session_id)

        # The engine has entered an in-memory state which has not crossed a
        # persistence boundary. Listing must not make it durable by rewriting
        # the session index from that snapshot.
        app.state.simulation_engine.begin_continuous(session_id)
        listed = client.get("/projects/fog-harbor/simulations", headers=AUTH)
        persisted_manifest = store.load(session_id)

    assert listed.status_code == 200
    assert listed.json()[0]["status"] == "created"
    assert persisted_manifest == durable_manifest


def test_simulation_api_checkpoint_branch_rollback(
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


def test_successful_turns_ignore_checkpoint_interval_for_exact_lineage(
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
            },
        },
    ).json()
    session_id = started["session_id"]

    first = _advance(client, session_id).json()
    head_after_first = client.get(
        "/projects/fog-harbor/branches/main",
        headers=AUTH,
    ).json()
    second = _advance(client, session_id).json()
    trace = client.get(
        "/projects/fog-harbor/branches/main/simulation-trace",
        headers=AUTH,
    ).json()

    assert first["checkpoint_id"] != started["checkpoint_id"]
    assert head_after_first["head_checkpoint_id"] == first["checkpoint_id"]
    assert trace[0]["checkpoint_id"] == first["checkpoint_id"]
    assert trace[1]["checkpoint_id"] == second["checkpoint_id"]
    assert second["checkpoint_id"] != first["checkpoint_id"]


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
    assert "/projects/{project_id}/simulations/{session_id}/projections" in paths
    assert (
        "/projects/{project_id}/simulations/{session_id}/projections/{task_id}/retry"
        in paths
    )
    assert (
        "/projects/{project_id}/simulations/{session_id}/maintenance/retry" not in paths
    )
    assert "/projects/{project_id}/simulations/{session_id}/terminate" in paths
    assert "/projects/{project_id}/simulations/{session_id}/locale" in paths
    assert "/projects/{project_id}/simulations/{session_id}/checkpoint" in paths
    assert "/projects/{project_id}/branches" in paths
    assert "/projects/{project_id}/branches/compare" in paths
    assert "/projects/{project_id}/branches/{branch_id}/rollback" in paths
