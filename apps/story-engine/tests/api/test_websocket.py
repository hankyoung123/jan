import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from story_engine.api.app import create_app
from story_engine.config import EngineSettings

TOKEN = "test-token"
PROTOCOLS = ["story-engine.v1", f"story-engine.token.{TOKEN}"]


def _app(tmp_path: Path):
    return create_app(
        EngineSettings(session_token=TOKEN, projects_root=tmp_path)
    )


def test_websocket_rejects_missing_or_invalid_session_token(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    with pytest.raises(WebSocketDisconnect) as missing, client.websocket_connect(
        "/ws/events",
        subprotocols=["story-engine.v1"],
    ):
        pass
    assert missing.value.code == 1008

    with pytest.raises(WebSocketDisconnect) as invalid, client.websocket_connect(
        "/ws/events",
        subprotocols=["story-engine.v1", "story-engine.token.wrong"],
    ):
        pass
    assert invalid.value.code == 1008

    with pytest.raises(WebSocketDisconnect) as duplicate, client.websocket_connect(
        "/ws/events",
        subprotocols=[
            "story-engine.v1",
            f"story-engine.token.{TOKEN}",
            f"story-engine.token.{TOKEN}",
        ],
    ):
        pass
    assert duplicate.value.code == 1008


def test_websocket_delivers_filtered_typed_event_envelopes(tmp_path: Path) -> None:
    app = _app(tmp_path)
    client = TestClient(app)

    with client.websocket_connect(
        "/ws/events?project_id=fog-harbor",
        subprotocols=PROTOCOLS,
    ) as socket:
        assert socket.accepted_subprotocol == "story-engine.v1"
        app.state.event_bus.publish(
            project_id="other-project",
            turn_id="turn-000001",
            event_type="turn.started",
            payload={},
        )
        expected = app.state.event_bus.publish(
            project_id="fog-harbor",
            turn_id="turn-000001",
            event_type="character.intent.completed",
            payload={"character_id": "chen-mo"},
        )
        received = socket.receive_json()

    assert received == expected.model_dump(mode="json")
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", received["event_id"])
    assert received["timestamp"].endswith("Z")
    assert received["project_id"] == "fog-harbor"
    assert received["turn_id"] == "turn-000001"
    assert received["type"] == "character.intent.completed"
    assert received["payload"] == {"character_id": "chen-mo"}
