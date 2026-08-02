import asyncio
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.events.stream import EngineEventBus

TOKEN = "test-token"
PROTOCOLS = ["story-engine.v1", f"story-engine.token.{TOKEN}"]


def _app(tmp_path: Path):
    return create_app(EngineSettings(session_token=TOKEN, projects_root=tmp_path))


def test_websocket_rejects_missing_or_invalid_session_token(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    with (
        pytest.raises(WebSocketDisconnect) as missing,
        client.websocket_connect(
            "/ws/events",
            subprotocols=["story-engine.v1"],
        ),
    ):
        pass
    assert missing.value.code == 1008

    with (
        pytest.raises(WebSocketDisconnect) as invalid,
        client.websocket_connect(
            "/ws/events",
            subprotocols=["story-engine.v1", "story-engine.token.wrong"],
        ),
    ):
        pass
    assert invalid.value.code == 1008

    with (
        pytest.raises(WebSocketDisconnect) as duplicate,
        client.websocket_connect(
            "/ws/events",
            subprotocols=[
                "story-engine.v1",
                f"story-engine.token.{TOKEN}",
                f"story-engine.token.{TOKEN}",
            ],
        ),
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
            subject_id="session:one",
            event_type="simulation.started",
            payload={},
        )
        expected = app.state.event_bus.publish(
            project_id="fog-harbor",
            subject_id="session:one",
            event_type="simulation.step.completed",
            payload={"acting_actor_id": "chen-mo"},
        )
        received = socket.receive_json()

    assert received == expected.model_dump(mode="json")
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", received["event_id"])
    assert received["timestamp"].endswith("Z")
    assert received["sequence"] == expected.sequence
    assert received["project_id"] == "fog-harbor"
    assert received["subject_id"] == "session:one"
    assert received["type"] == "simulation.step.completed"
    assert received["payload"] == {"acting_actor_id": "chen-mo"}


def test_event_sequence_is_monotonic_and_queue_overflow_requires_resync() -> None:
    async def exercise() -> None:
        bus = EngineEventBus(queue_size=2)
        subscriber = bus.subscribe("fog-harbor")
        try:
            published = [
                bus.publish(
                    project_id="fog-harbor",
                    subject_id="session:one",
                    event_type="simulation.started",
                    payload={"index": index},
                )
                for index in range(3)
            ]
            await asyncio.sleep(0)
            queued = []
            while not subscriber.queue.empty():
                queued.append(subscriber.queue.get_nowait())
        finally:
            bus.unsubscribe(subscriber)

        assert [event.sequence for event in published] == [1, 2, 3]
        assert len(queued) == 1
        assert queued[0].sequence == 3
        assert queued[0].type == "stream.resync_required"
        assert queued[0].payload["reason"] == "subscriber_queue_overflow"

    asyncio.run(exercise())
