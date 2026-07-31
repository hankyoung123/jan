from pathlib import Path

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.submission.service import fog_harbor_submission
from story_engine.workspace.project_store import ProjectStore

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WS_HEADERS = {
    "Sec-WebSocket-Protocol": f"story-engine.v1, story-engine.token.{TOKEN}"
}


def _app(tmp_path: Path):
    return create_app(
        EngineSettings(session_token=TOKEN, projects_root=tmp_path / "projects")
    )


def test_project_lifecycle_lists_opens_and_closes_workspace(tmp_path: Path) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        created = client.post(
            "/submissions/finalize",
            headers=AUTH,
            json=fog_harbor_submission().model_dump(mode="json"),
        )
        assert created.status_code == 201

        catalog = client.get("/projects", headers=AUTH)
        assert catalog.status_code == 200
        assert catalog.json() == [
            {
                "id": "fog-harbor",
                "title": "雾港",
                "genre": "悬疑",
                "world_version": 0,
                "is_open": True,
            }
        ]

        current = client.get("/projects/fog-harbor/workspace", headers=AUTH)
        assert current.status_code == 200
        assert current.json()["index"]["project_id"] == "fog-harbor"

        closed = client.post("/projects/fog-harbor/close", headers=AUTH)
        assert closed.status_code == 200
        assert closed.json() == {"project_id": "fog-harbor", "status": "closed"}
        assert client.get(
            "/projects/fog-harbor/workspace", headers=AUTH
        ).status_code == 409

        reopened = client.post("/projects/fog-harbor/open", headers=AUTH)
        assert reopened.status_code == 200
        assert reopened.json()["status"] == "open"


def test_external_markdown_change_refreshes_index_and_emits_event(
    tmp_path: Path,
) -> None:
    app = _app(tmp_path)
    with TestClient(app) as client:
        client.post(
            "/submissions/finalize",
            headers=AUTH,
            json=fog_harbor_submission().model_dump(mode="json"),
        )
        root = tmp_path / "projects/fog-harbor"

        with client.websocket_connect(
            "/ws/events?project_id=fog-harbor",
            headers=WS_HEADERS,
            subprotocols=["story-engine.v1"],
        ) as websocket:
            world = ProjectStore(root).load().world.model_copy(
                update={"current_location": "灯塔", "version": 1}
            )
            ProjectStore(root).save_world(world)
            refreshed = app.state.workspace_manager.refresh("fog-harbor")
            event = websocket.receive_json()

        assert refreshed.index.world_version == 1
        assert event["type"] == "workspace.changed"
        assert event["turn_id"] == "workspace"
        assert event["payload"]["changed_paths"] == ["world.md"]
        assert event["payload"]["world_version"] == 1
