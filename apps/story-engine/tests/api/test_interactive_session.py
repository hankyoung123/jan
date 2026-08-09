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
from story_engine.persistence.session_store import SessionStore

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

    def execute_step(
        self,
        step: int,
        *,
        cancellation: Event,
        eligible_actor_ids: tuple[str, ...] | None = None,
    ) -> StepResult:
        assert not cancellation.is_set()
        assert eligible_actor_ids == ("zhang-ye",)
        event = ResolvedEvent(
            event_id=f"event:{self.session_id}:{step}",
            session_id=self.session_id,
            step=step,
            actor_id="zhang-ye",
            event_text="张野避开了你的视线，朝旅馆门口走去。",
            visibility=EventVisibility.PARTICIPANTS,
            participant_ids=("player", "zhang-ye"),
            content_locale="zh-CN",
            occurred_at=datetime.now(UTC),
        )
        return StepResult(
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            acting_actor_id="zhang-ye",
            action_spec=None,
            action_text="我离开旅馆。",
            resolved_turn=ResolvedTurn(
                session_id=self.session_id,
                branch_id=self.branch_id,
                step=step,
                acting_actor_id="zhang-ye",
                putative_event_text="我离开旅馆。",
                raw_resolution_text=event.event_text,
                events=(event,),
                content_locale="zh-CN",
            ),
            status=TurnSessionStatus.RUNNING,
        )

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


def test_case_01_interactive_turn_treats_asserted_death_as_an_intent(
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
    assert checkpoint.current_step == 2
    assert checkpoint.player_actor_id == "player"


def test_cases_06_and_10_npc_intent_is_resolved_and_can_act_autonomously(
    tmp_path,
) -> None:
    app = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path),
        simulation_runtime_factory=lambda session_id, request: InteractiveRuntime(
            session_id, request
        ),  # type: ignore[arg-type]
    )

    with TestClient(app) as client:
        client.get(
            "/projects/last-ferry-before/simulation/session",
            headers=AUTH,
        )
        response = client.post(
            "/projects/last-ferry-before/simulation/turn",
            headers=AUTH,
            json={"text": "我杀了张野。"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["visible_events"] == [
        "你试图攻击张野，但他后退躲开了；张野没有死亡。",
        "张野避开了你的视线，朝旅馆门口走去。",
    ]
    checkpoint = CheckpointStore(tmp_path / "last-ferry-before").load(
        body["checkpoint_id"]
    )
    assert checkpoint.current_step == 2


def test_case_12_interactive_branch_resumes_without_moving_main(
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
        ).json()
        main_turn = client.post(
            "/projects/last-ferry-before/simulation/turn",
            headers=AUTH,
            json={"text": "我杀了张野。"},
        ).json()
        fork = client.post(
            "/projects/last-ferry-before/branches",
            headers=AUTH,
            json={
                "branch_id": "alternate",
                "source_checkpoint_id": main_turn["checkpoint_id"],
                "parent_branch_id": "main",
                "content_locale": "zh-CN",
            },
        )
        timeline = client.get(
            "/projects/last-ferry-before/branches/alternate/timeline",
            headers=AUTH,
        )
        reopened = client.get(
            "/projects/last-ferry-before/simulation/session?branch_id=alternate",
            headers=AUTH,
        )
        alternate_turn = client.post(
            "/projects/last-ferry-before/simulation/turn?branch_id=alternate",
            headers=AUTH,
            json={"text": "我用相机长焦从楼下观察二楼窗户。"},
        )
        branches = client.get(
            "/projects/last-ferry-before/branches",
            headers=AUTH,
        ).json()

    assert fork.status_code == 201
    assert timeline.status_code == 200
    timeline_entries = timeline.json()
    assert [entry["step"] for entry in timeline_entries] == [0, 1, 2]
    assert timeline_entries[0]["checkpoint_id"] == opened["checkpoint_id"]
    assert timeline_entries[-1] == {
        "checkpoint_id": main_turn["checkpoint_id"],
        "step": 2,
        "world_time": "18:43",
        "is_current": True,
    }
    assert all(entry["is_current"] is False for entry in timeline_entries[:-1])
    assert reopened.status_code == 200
    assert reopened.json()["checkpoint_id"] == main_turn["checkpoint_id"]
    assert alternate_turn.status_code == 200
    assert alternate_turn.json()["checkpoint_id"] != main_turn["checkpoint_id"]
    heads = {branch["branch_id"]: branch["head_checkpoint_id"] for branch in branches}
    assert heads["main"] == main_turn["checkpoint_id"]
    assert heads["alternate"] == alternate_turn.json()["checkpoint_id"]


def test_interactive_session_reopens_a_terminated_branch_head(tmp_path) -> None:
    settings = EngineSettings(session_token="test-token", projects_root=tmp_path)
    app = create_app(
        settings,
        simulation_runtime_factory=lambda session_id, request: InteractiveRuntime(
            session_id, request
        ),  # type: ignore[arg-type]
    )
    with TestClient(app) as client:
        opened = client.get(
            "/projects/last-ferry-before/simulation/session",
            headers=AUTH,
        )
        session_id = SessionStore(tmp_path / "last-ferry-before").list(
            "last-ferry-before"
        )[0].session_id
        terminated = client.post(
            f"/projects/last-ferry-before/simulations/{session_id}/terminate",
            headers=AUTH,
            json={"reason_text": "测试结束"},
        )

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
        continued = client.post(
            "/projects/last-ferry-before/simulation/turn",
            headers=AUTH,
            json={"text": "继续观察大厅"},
        )

    assert opened.status_code == 200
    assert terminated.status_code == 200
    assert terminated.json()["status"] == "terminated"
    assert restored.status_code == 200
    assert continued.status_code == 200


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
    assert checkpoint.current_step == 62
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
