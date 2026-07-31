from pathlib import Path

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.submission.service import fog_harbor_submission

AUTH = {"Authorization": "Bearer test-token"}


def test_project_and_turn_approval_flow(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            EngineSettings(
                session_token="test-token",
                projects_root=tmp_path,
            )
        )
    )

    created = client.post(
        "/submissions/finalize",
        headers=AUTH,
        json=fog_harbor_submission().model_dump(mode="json"),
    )
    assert created.status_code == 201
    assert created.json()["project"]["id"] == "fog-harbor"

    turn = client.post(
        "/projects/fog-harbor/turns/generate",
        headers=AUTH,
        json={},
    )
    assert turn.status_code == 201
    assert turn.json()["status"] == "reviewed"
    assert len(turn.json()["intents"]) == 2

    approved = client.post(
        "/projects/fog-harbor/turns/turn-000001/confirm",
        headers=AUTH,
    )
    assert approved.status_code == 200
    assert approved.json()["candidate"]["status"] == "committed"
    assert approved.json()["event"]["id"] == "event-000001"

    project = client.get("/projects/fog-harbor", headers=AUTH)
    assert project.status_code == 200
    assert project.json()["world"]["version"] == 1
    assert all(character["version"] == 1 for character in project.json()["characters"])


def test_turn_api_exposes_only_revision_confirmation_and_discard_actions(
    tmp_path: Path,
) -> None:
    app = create_app(
        EngineSettings(session_token="test-token", projects_root=tmp_path)
    )
    paths = app.openapi()["paths"]

    assert "/projects/{project_id}/turns/{turn_id}/request-revision" in paths
    assert "/projects/{project_id}/turns/{turn_id}/confirm" in paths
    assert "/projects/{project_id}/turns/{turn_id}/discard" in paths
    assert "/projects/{project_id}/turns/{turn_id}/review" not in paths
    assert "/projects/{project_id}/turns/{turn_id}/approve" not in paths
    assert "/projects/{project_id}/turns" not in paths


def test_unrunnable_submission_returns_conflict_without_project(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            EngineSettings(session_token="test-token", projects_root=tmp_path)
        )
    )
    payload = fog_harbor_submission().model_dump(mode="json")
    payload["pressures"] = []
    for character in payload["characters"]:
        character["current_goal"] = "等待天亮"

    response = client.post("/submissions/finalize", headers=AUTH, json=payload)

    assert response.status_code == 409
    assert not (tmp_path / "fog-harbor").exists()


def test_project_routes_require_session_token(tmp_path: Path) -> None:
    client = TestClient(
        create_app(
            EngineSettings(
                session_token="test-token",
                projects_root=tmp_path,
            )
        )
    )

    response = client.post(
        "/submissions/finalize",
        json=fog_harbor_submission().model_dump(mode="json"),
    )

    assert response.status_code == 401
    assert not (tmp_path / "fog-harbor").exists()
