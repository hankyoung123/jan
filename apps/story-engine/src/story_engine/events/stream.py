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
    "workspace.changed",
    "simulation.started",
    "simulation.step.completed",
    "simulation.paused",
    "simulation.checkpointed",
    "simulation.terminated",
    "simulation.failed",
    "stream.resync_required",
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
    subject_id: str = Field(min_length=1)
    timestamp: datetime
    sequence: int = Field(ge=1)
    type: EngineEventType
    payload: dict[str, JsonValue]


class _Subscriber:
    def __init__(self, project_id: str | None, queue_size: int) -> None:
        self.project_id = project_id
        self.loop = asyncio.get_running_loop()
        self.queue: asyncio.Queue[EngineEvent] = asyncio.Queue(maxsize=queue_size)


class EngineEventBus:
    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        queue_size: int = 256,
    ) -> None:
        if queue_size < 2:
            raise ValueError("event queue size must be at least two")
        self.clock = clock or (lambda: datetime.now(UTC))
        self.queue_size = queue_size
        self._subscribers: set[_Subscriber] = set()
        self._lock = Lock()
        self._sequence = 0

    def subscribe(self, project_id: str | None) -> _Subscriber:
        subscriber = _Subscriber(project_id, self.queue_size)
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
        subject_id: str,
        event_type: EngineEventType,
        payload: dict[str, JsonValue],
    ) -> EngineEvent:
        with self._lock:
            self._sequence += 1
            timestamp = self.clock()
            event = EngineEvent(
                event_id=_ulid(timestamp),
                project_id=project_id,
                subject_id=subject_id,
                timestamp=timestamp,
                sequence=self._sequence,
                type=event_type,
                payload=payload,
            )
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

    @property
    def sequence(self) -> int:
        with self._lock:
            return self._sequence

    @staticmethod
    def _deliver(subscriber: _Subscriber, event: EngineEvent) -> None:
        if subscriber.queue.full():
            while not subscriber.queue.empty():
                subscriber.queue.get_nowait()
            subscriber.queue.put_nowait(
                event.model_copy(
                    update={
                        "type": "stream.resync_required",
                        "payload": {
                            "reason": "subscriber_queue_overflow",
                            "latest_sequence": event.sequence,
                        },
                    }
                )
            )
            return
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
    after_sequence: int | None = None,
) -> None:
    await websocket.accept(subprotocol="story-engine.v1")
    subscriber = event_bus.subscribe(project_id)
    try:
        if (
            after_sequence is not None
            and project_id is not None
            and after_sequence < event_bus.sequence
        ):
            timestamp = event_bus.clock()
            await websocket.send_json(
                EngineEvent(
                    event_id=_ulid(timestamp),
                    project_id=project_id,
                    subject_id="stream",
                    timestamp=timestamp,
                    sequence=event_bus.sequence,
                    type="stream.resync_required",
                    payload={
                        "reason": "events_missed_while_disconnected",
                        "after_sequence": after_sequence,
                    },
                ).model_dump(mode="json")
            )
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
