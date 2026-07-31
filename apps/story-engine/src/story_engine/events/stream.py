import asyncio
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from secrets import compare_digest, token_bytes
from threading import Lock
from typing import Literal

from fastapi import WebSocket
from pydantic import Field, JsonValue
from starlette.websockets import WebSocketDisconnect

from story_engine.domain.models import DomainModel

EngineEventType = Literal[
    "engine.status",
    "turn.started",
    "character.intent.started",
    "character.intent.delta",
    "character.intent.completed",
    "resolver.started",
    "resolver.completed",
    "review.started",
    "review.completed",
    "turn.failed",
    "turn.cancelled",
]

EventSink = Callable[[EngineEventType, dict[str, JsonValue]], None]

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid(timestamp: datetime) -> str:
    milliseconds = int(timestamp.timestamp() * 1000)
    value = (milliseconds << 80) | int.from_bytes(token_bytes(10), "big")
    characters = ["0"] * 26
    for index in range(25, -1, -1):
        characters[index] = _CROCKFORD[value & 31]
        value >>= 5
    return "".join(characters)


class EngineEvent(DomainModel):
    event_id: str = Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")
    project_id: str = Field(min_length=1)
    turn_id: str = Field(min_length=1)
    timestamp: datetime
    type: EngineEventType
    payload: dict[str, JsonValue]


class _Subscriber:
    def __init__(self, project_id: str | None) -> None:
        self.project_id = project_id
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=256)


class EngineEventBus:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.clock = clock or (lambda: datetime.now(UTC))
        self._subscribers: set[_Subscriber] = set()
        self._lock = Lock()

    def subscribe(self, project_id: str | None) -> _Subscriber:
        subscriber = _Subscriber(project_id)
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: _Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    def publish(
        self,
        *,
        project_id: str,
        turn_id: str,
        event_type: EngineEventType,
        payload: dict[str, JsonValue],
    ) -> EngineEvent:
        timestamp = self.clock()
        event = EngineEvent(
            event_id=_ulid(timestamp),
            project_id=project_id,
            turn_id=turn_id,
            timestamp=timestamp,
            type=event_type,
            payload=payload,
        )
        with self._lock:
            subscribers = tuple(self._subscribers)
        for subscriber in subscribers:
            if subscriber.project_id not in {None, project_id}:
                continue
            subscriber.loop.call_soon_threadsafe(
                self._deliver,
                subscriber,
                event,
            )
        return event

    @staticmethod
    def _deliver(subscriber: _Subscriber, event: EngineEvent) -> None:
        if subscriber.queue.full():
            subscriber.queue.get_nowait()
        subscriber.queue.put_nowait(event)


def websocket_token_is_valid(websocket: WebSocket, expected: str) -> bool:
    protocols = [
        item.strip()
        for item in websocket.headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    ]
    prefix = "story-engine.token."
    supplied = [
        item.removeprefix(prefix) for item in protocols if item.startswith(prefix)
    ]
    return (
        protocols.count("story-engine.v1") == 1
        and len(supplied) == 1
        and compare_digest(supplied[0], expected)
    )


async def stream_events(
    websocket: WebSocket,
    event_bus: EngineEventBus,
    *,
    project_id: str | None,
) -> None:
    await websocket.accept(subprotocol="story-engine.v1")
    subscriber = event_bus.subscribe(project_id)
    try:
        while True:
            event_task = asyncio.create_task(subscriber.queue.get())
            disconnect_task = asyncio.create_task(websocket.receive())
            done, pending = await asyncio.wait(
                {event_task, disconnect_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if disconnect_task in done:
                return
            event = event_task.result()
            await websocket.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
    finally:
        event_bus.unsubscribe(subscriber)
