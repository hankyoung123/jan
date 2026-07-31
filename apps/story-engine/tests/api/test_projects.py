from pathlib import Path

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings

AUTH = {"Authorization": "Bearer test-token"}


def _project_payload() -> dict[str, object]:
    return {
        "id": "fog-harbor",
        "title": "雾港",
        "genre": "悬疑",
        "theme": "真相与亲情之间的选择",
        "tone": "克制",
        "world": {
            "current_time": "暴风雨前夜",
            "current_location": "雾港",
            "version": 0,
        },
        "characters": [
            {
                "id": "chen-mo",
                "type": "active",
                "identity": "机械工程师",
                "core_desire": "查明真相",
                "current_goal": "检查灯塔",
                "location": "港务所",
                "version": 0,
            }
        ],
    }


def _turn_payload() -> dict[str, object]:
    return {
        "id": "turn-000001",
        "project_id": "fog-harbor",
        "base_world_version": 0,
        "base_character_versions": {"chen-mo": 0},
        "intents": [
            {
                "character_id": "chen-mo",
                "action": "前往灯塔",
                "target": "灯塔",
                "goal": "检查灯芯槽",
                "knowledge_basis": ["fact:lighthouse-never-off"],
            }
        ],
        "outcome": {
            "summary": "陈默抵达灯塔。",
            "character_changes": [
                {
                    "target_type": "character",
                    "target_id": "chen-mo",
                    "field": "location",
                    "old_value": "港务所",
                    "new_value": "灯塔一层",
                    "reason": "角色移动",
                }
            ],
        },
    }


def test_project_and_turn_approval_flow(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            EngineSettings(
                session_token="test-token",
                projects_root=tmp_path,
            )
        )
    )

    created = client.post("/projects", headers=AUTH, json=_project_payload())
    assert created.status_code == 201
    assert created.json()["project"]["id"] == "fog-harbor"

    turn = client.post(
        "/projects/fog-harbor/turns",
        headers=AUTH,
        json=_turn_payload(),
    )
    assert turn.status_code == 201
    assert turn.json()["status"] == "draft"

    reviewed = client.post(
        "/projects/fog-harbor/turns/turn-000001/review",
        headers=AUTH,
        json={
            "mode": "turn_review",
            "passed": True,
            "summary": "检查通过。",
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["status"] == "reviewed"

    approved = client.post(
        "/projects/fog-harbor/turns/turn-000001/approve",
        headers=AUTH,
    )
    assert approved.status_code == 200
    assert approved.json()["candidate"]["status"] == "committed"
    assert approved.json()["event"]["id"] == "event-000001"

    project = client.get("/projects/fog-harbor", headers=AUTH)
    assert project.status_code == 200
    assert project.json()["characters"][0]["location"] == "灯塔一层"


def test_project_routes_require_session_token(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            EngineSettings(
                session_token="test-token",
                projects_root=tmp_path,
            )
        )
    )

    response = client.post("/projects", json=_project_payload())

    assert response.status_code == 401
    assert not (tmp_path / "fog-harbor").exists()

