from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from collections.abc import AsyncIterator, Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

from story_engine.domain.models import (
    Character,
    CharacterIntent,
    FactCandidate,
    StateChange,
    StoryEvent,
    WorldOutcome,
    WorldState,
)
from story_engine.evolution.context import CharacterContext
from story_engine.evolution.service import EvolutionService
from story_engine.manuscript.models import Scene
from story_engine.models.contracts import Message, ModelRequest, ModelStreamChunk
from story_engine.models.gateway import ModelGateway
from story_engine.models.registry import ProfileRegistry
from story_engine.rag.models import RagSearchRequest, RetrievalScope
from story_engine.rag.service import RagService
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import render_scene
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.session import WorkspaceChange, WorkspaceSessionManager


class DelayedMockTransport:
    def __init__(self, delay: float) -> None:
        self.delay = delay

    async def complete(
        self, payload: Mapping[str, Any], *, timeout_seconds: int
    ) -> Mapping[str, Any]:
        del payload, timeout_seconds
        await asyncio.sleep(self.delay)
        return {
            "choices": [
                {
                    "message": {"content": '{"ok":true}'},
                    "finish_reason": "stop",
                }
            ]
        }

    async def stream(
        self, payload: Mapping[str, Any], *, timeout_seconds: int
    ) -> AsyncIterator[ModelStreamChunk]:
        del payload, timeout_seconds
        await asyncio.sleep(self.delay)
        yield ModelStreamChunk(delta='{"ok":true}', finish_reason="stop")


class DelayedTurnGenerator:
    def __init__(self, delay: float) -> None:
        self.delay = delay

    def generate_intent(self, context: CharacterContext) -> CharacterIntent:
        time.sleep(self.delay)
        return CharacterIntent(
            character_id=context.character.id,
            action="观察当前局势并报告异常",
            target=context.perception.perceived_location,
            goal=context.character.current_goal or context.character.core_desire,
            knowledge_basis=(context.perception.visible_facts[0].id,),
        )

    def resolve(
        self,
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        characters: tuple[Character, ...],
    ) -> WorldOutcome:
        del characters
        time.sleep(self.delay)
        round_value = world.world_variables.get("round", 0)
        assert isinstance(round_value, int) and not isinstance(round_value, bool)
        return WorldOutcome(
            summary=f"{len(intents)} 名角色完成观察。",
            fact_candidates=(
                FactCandidate(
                    id=f"fact:benchmark-{round_value + 1:06d}",
                    statement="在场角色完成本轮观察。",
                    visibility="public",
                ),
            ),
            world_changes=(
                StateChange(
                    target_type="world",
                    target_id="world",
                    field="world_variables.round",
                    old_value=round_value,
                    new_value=round_value + 1,
                    reason="统一结算观察意图",
                ),
            ),
        )

    def revise(
        self,
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        characters: tuple[Character, ...],
        previous_outcome: WorldOutcome,
        instruction: str,
    ) -> WorldOutcome:
        del world, intents, characters, instruction
        return previous_outcome


def measured(
    values: dict[str, float | int], label: str, operation: Callable[[], Any]
) -> Any:
    started = time.perf_counter()
    result = operation()
    values[f"{label}_seconds"] = time.perf_counter() - started
    return result


def seed(root: Path, events: int, scenes: int, characters: int) -> Path:
    SubmissionService(root).finalize(fog_harbor_submission())
    project_root = root / "fog-harbor"
    store = ProjectStore(project_root)
    snapshot = store.load()
    public_fact_id = snapshot.world.public_fact_ids[0]
    for index in range(len(snapshot.characters) + 1, characters + 1):
        store.save_character(
            Character(
                id=f"benchmark-{index:03d}",
                display_name=f"基准角色 {index}",
                type="active",
                identity="性能基准观察员",
                core_desire="维持局势可追踪",
                current_goal="记录当前异常",
                known_fact_ids=(public_fact_id,),
                location=snapshot.world.current_location,
            ),
            overwrite=False,
        )
    event_store = EventStore(project_root)
    started_at = datetime(2026, 1, 1, tzinfo=UTC)
    for sequence in range(1, events + 1):
        event_store.append(
            StoryEvent(
                id=f"event-{sequence:06d}",
                sequence=sequence,
                occurred_at=started_at + timedelta(minutes=sequence),
                summary=f"长篇归档事件 {sequence}: 灯塔运行记录。",
                participants=("chen-mo", "lin-lan"),
                source_turn_id=f"archived-turn-{sequence:06d}",
                approved_by_user=True,
            )
        )
    for sequence in range(1, scenes + 1):
        source = min(events, max(1, sequence * 2))
        scene = Scene(
            id=f"scene-{sequence:06d}",
            project_id=snapshot.project.id,
            sequence=sequence,
            chapter_id=f"chapter-{((sequence - 1) // 20) + 1}",
            title=f"基准场景 {sequence}",
            body=f"灯塔归档正文片段 {sequence}。",
            source_event_ids=(f"event-{source:06d}",),
            version=1,
        )
        atomic_write_text(
            project_root / "scenes" / f"{sequence:06d}.md",
            render_scene(scene),
            overwrite=False,
        )
    snapshot = store.load()
    last_event_id = f"event-{events:06d}"
    store.save_world(
        snapshot.world.model_copy(
            update={
                "version": events,
                "world_variables": {
                    **snapshot.world.world_variables,
                    "round": events,
                },
            }
        )
    )
    for character in snapshot.characters:
        store.save_character(
            character.model_copy(
                update={"version": events, "last_event_id": last_event_id}
            )
        )
    return project_root


async def delayed_model_call(root: Path, delay: float) -> None:
    gateway = ModelGateway(
        ProfileRegistry(root / ".story-engine/benchmark-models.json"),
        DelayedMockTransport(delay),
    )
    await gateway.complete(
        ModelRequest(
            profile_id="editor",
            task_type="editor",
            messages=(Message(role="user", content="benchmark"),),
            output_schema=(
                '{"type":"object","properties":{"ok":{"type":"boolean"}},'
                '"required":["ok"],"additionalProperties":false}'
            ),
            max_output_tokens=64,
            timeout_seconds=30,
        )
    )


def run(args: argparse.Namespace) -> dict[str, float | int]:
    values: dict[str, float | int] = {
        "events": args.events,
        "scenes": args.scenes,
        "characters": args.characters,
    }
    with tempfile.TemporaryDirectory(prefix="story-engine-benchmark-") as temp:
        projects_root = Path(temp)
        root = measured(
            values,
            "seed",
            lambda: seed(
                projects_root, args.events, args.scenes, args.characters
            ),
        )
        changed = Event()

        def on_change(change: WorkspaceChange) -> None:
            if change.status == "ready" and "world.md" in change.changed_paths:
                changed.set()

        manager = WorkspaceSessionManager(
            projects_root,
            poll_interval=0.1,
            debounce_interval=0.5,
            on_change=on_change,
        )
        try:
            measured(values, "open", lambda: manager.open("fog-harbor"))
            rag = RagService(root)
            measured(values, "rag_rebuild", rag.rebuild_index)
            measured(
                values,
                "rag_search",
                lambda: rag.search(
                    RagSearchRequest(
                        query="灯塔运行记录",
                        scope=RetrievalScope(kind="editorial"),
                    )
                ),
            )
            delay = args.model_delay_ms / 1000
            measured(
                values,
                "model",
                lambda: asyncio.run(delayed_model_call(root, delay)),
            )
            evolution = EvolutionService(
                root, generator=DelayedTurnGenerator(delay)
            )
            candidate = measured(values, "generate", evolution.generate_turn)
            measured(values, "commit", lambda: evolution.confirm(candidate.id))
            manager.refresh("fog-harbor")
            changed.clear()
            snapshot = ProjectStore(root).load()
            started = time.perf_counter()
            ProjectStore(root).save_world(
                snapshot.world.model_copy(
                    update={
                        "active_pressures": (
                            *snapshot.world.active_pressures,
                            "外部基准修改",
                        )
                    }
                )
            )
            if not changed.wait(timeout=15):
                raise TimeoutError("watcher did not observe world.md")
            values["external_refresh_seconds"] = time.perf_counter() - started
        finally:
            manager.close_all()
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description="Long-project performance benchmark")
    parser.add_argument("--events", type=int, default=10_000)
    parser.add_argument("--scenes", type=int, default=500)
    parser.add_argument("--characters", type=int, default=50)
    parser.add_argument("--model-delay-ms", type=int, default=50)
    args = parser.parse_args()
    if args.events < 1 or args.scenes < 1 or args.characters < 2:
        parser.error("events/scenes must be positive; characters must be at least 2")
    print(json.dumps(run(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
