# ruff: noqa: RUF001

from datetime import UTC, datetime
from threading import Event

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.domain.models import Character, WorldState
from story_engine.domain.projection import EventVisibility, ResolvedEvent, ResolvedTurn
from story_engine.domain.simulation import (
    StepResult,
    TurnSessionRequest,
    TurnSessionStatus,
)
from story_engine.persistence.checkpoint_store import CheckpointStore

AUTH = {"Authorization": "Bearer test-token"}


class InteractiveRuntime:
    def __init__(self, session_id: str, request: TurnSessionRequest) -> None:
        self.session_id = session_id
        self.branch_id = request.branch_id
        self.player_actor_id = request.player_actor_id
        self.cancellation = Event()
        self._characters = (
            Character(
                id="player",
                display_name="你",
                type="active",
                identity="本地调查记者",
                core_desire="查明真相",
                current_goal="调查",
                capabilities=("摄影",),
                conditions=("右手轻伤",),
                resources=("相机", "手机"),
            ),
            Character(
                id="zhang-ye",
                display_name="张野",
                type="active",
                identity="货运承包人",
                core_desire="离开港口",
                current_goal="赶上末班船",
            ),
        )
        self._world = WorldState(
            current_time="18:43",
            current_location="港口旅馆",
            scene_text="雨水浸透了门口的地毯。",
        )

    def execute_step(self, step: int, *, cancellation: Event) -> StepResult:
        del step, cancellation
        raise AssertionError("interactive endpoint must not start an autonomous step")

    def execute_human_turn(
        self,
        step: int,
        *,
        text: str,
        cancellation: Event,
    ) -> StepResult:
        assert not cancellation.is_set()
        assert self.player_actor_id == "player"
        event = ResolvedEvent(
            event_id=f"event:{self.session_id}:{step}",
            session_id=self.session_id,
            step=step,
            actor_id="player",
            event_text=(
                "你试图攻击张野，但他后退躲开了；张野没有死亡。"
                if text == "我杀了张野。"
                else "你观察着旅馆大厅，张野仍在柜台附近。"
            ),
            visibility=EventVisibility.PARTICIPANTS,
            participant_ids=("player", "zhang-ye"),
            content_locale="zh-CN",
            occurred_at=datetime.now(UTC),
        )
        return StepResult(
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            acting_actor_id="player",
            action_spec=None,
            action_text=text,
            resolved_turn=ResolvedTurn(
                session_id=self.session_id,
                branch_id=self.branch_id,
                step=step,
                acting_actor_id="player",
                putative_event_text=text,
                raw_resolution_text=event.event_text,
                events=(event,),
                content_locale="zh-CN",
            ),
            status=TurnSessionStatus.RUNNING,
        )

    def actor_states(self):
        return {}

    def game_master_states(self):
        return {}

    def memory_snapshots(self):
        return {}

    def roster_actor_ids(self):
        return ("player", "zhang-ye")

    def character_states(self):
        return self._characters

    def world_state(self):
        return self._world

    def pending_scene_events(self):
        return ()

    def restore_states(self, *, actor_states, game_master_states, memory_snapshots):
        del actor_states, game_master_states, memory_snapshots

    def set_content_locale(self, content_locale: str) -> None:
        self.content_locale = content_locale

    def set_observer(self, observer) -> None:
        self.observer = observer

    def drain_stage_events(self):
        return ()


def test_interactive_turn_creates_default_world_and_returns_only_perception(
    tmp_path,
) -> None:
    app = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path),
        simulation_runtime_factory=lambda session_id, request: InteractiveRuntime(
            session_id, request
        ),  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        opened = client.get(
            "/projects/last-ferry-before/simulation/session",
            headers=AUTH,
        )
        response = client.post(
            "/projects/last-ferry-before/simulation/turn",
            headers=AUTH,
            json={"text": "我杀了张野。"},
        )

    assert opened.status_code == 200
    assert opened.json()["player_state"]["possessions"] == ["相机", "手机"]
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "perception",
        "visible_events",
        "player_state",
        "checkpoint_id",
        "world_time",
    }
    assert "死亡" in body["visible_events"][0]
    assert "actor_states" not in str(body)
    checkpoint = CheckpointStore(tmp_path / "last-ferry-before").load(
        body["checkpoint_id"]
    )
    assert checkpoint.current_step == 1
    assert checkpoint.player_actor_id == "player"


def test_interactive_session_survives_thirty_one_turns_and_reopens_from_branch_head(
    tmp_path,
) -> None:
    settings = EngineSettings(session_token="test-token", projects_root=tmp_path)
    app = create_app(
        settings,
        simulation_runtime_factory=lambda session_id, request: InteractiveRuntime(
            session_id, request
        ),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        client.get(
            "/projects/last-ferry-before/simulation/session",
            headers=AUTH,
        )
        for index in range(31):
            response = client.post(
                "/projects/last-ferry-before/simulation/turn",
                headers=AUTH,
                json={"text": f"我观察大厅第 {index} 次。"},
            )
            assert response.status_code == 200
        final_checkpoint_id = response.json()["checkpoint_id"]

    checkpoint = CheckpointStore(tmp_path / "last-ferry-before").load(
        final_checkpoint_id
    )
    assert checkpoint.current_step == 31
    assert checkpoint.world is not None
    assert checkpoint.characters[0].resources == ("相机", "手机")

    reopened = create_app(
        settings,
        simulation_runtime_factory=lambda session_id, request: InteractiveRuntime(
            session_id, request
        ),  # type: ignore[arg-type]
    )
    with TestClient(reopened) as client:
        restored = client.get(
            "/projects/last-ferry-before/simulation/session",
            headers=AUTH,
        )
    assert restored.status_code == 200
    assert restored.json()["checkpoint_id"] == final_checkpoint_id
