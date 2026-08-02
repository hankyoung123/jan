import json
from pathlib import Path

from pydantic import JsonValue

from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.simulation import TurnSessionSnapshot
from story_engine.persistence.simulation_log import SimulationLogRecord
from story_engine.workspace.transaction import AtomicBatch


def _json_block(value: dict[str, JsonValue]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _checkpoint_world_events(
    snapshot: TurnSessionSnapshot,
) -> tuple[MemoryRecord, ...]:
    """Recover canonical history carried by a checkpoint across branch forks."""

    events: dict[str, MemoryRecord] = {}
    for memory_snapshot in snapshot.memory_snapshots.values():
        if memory_snapshot.scope != MemoryScope.GAME_MASTER:
            continue
        bank = ConcordiaMemoryBank(
            owner_id=memory_snapshot.owner_id,
            scope=MemoryScope.GAME_MASTER,
        )
        bank.restore(memory_snapshot)
        for record in bank.scan(
            lambda item: (
                item.record_type == MemoryRecordType.WORLD_EVENT
                and item.step < snapshot.current_step
            )
        ):
            events[record.record_id] = record
    return tuple(
        sorted(
            events.values(),
            key=lambda event: (event.step, event.created_at, event.record_id),
        )
    )


class MarkdownProjector:
    """Render disposable Markdown views from a checkpoint and its raw log."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def render(
        self,
        snapshot: TurnSessionSnapshot,
        records: tuple[SimulationLogRecord, ...],
        *,
        branch_id: str | None = None,
    ) -> tuple[Path, ...]:
        target_branch_id = branch_id or snapshot.branch_id
        eligible = tuple(
            record
            for record in records
            if record.result.branch_id == target_branch_id
            and record.result.step < snapshot.current_step
        )
        directory = Path(".story-engine/projections") / target_branch_id
        world_path = directory / "world.md"
        timeline_path = directory / "timeline.md"
        characters_path = directory / "characters.md"

        event_sections: list[tuple[int, str]] = []
        timeline_lines: list[tuple[int, str]] = []
        projected_event_ids: set[str] = set()
        resolved_steps: set[int] = set()
        for record in eligible:
            result = record.result
            turn = result.resolved_turn
            if turn is not None:
                resolved_steps.add(result.step)
                event_sections.append(
                    (
                        result.step,
                        f"## Step {result.step}\n\n{turn.raw_resolution_text}\n",
                    )
                )
                for event in turn.events:
                    projected_event_ids.add(event.event_id)
                    timeline_lines.append(
                        (
                            event.step,
                            f"- Step {event.step} · `{event.event_id}` · "
                            f"{event.event_text}",
                        )
                    )
            elif result.action_text:
                timeline_lines.append(
                    (
                        result.step,
                        f"- Step {result.step} · `{result.acting_actor_id}` · "
                        f"{result.action_text}",
                    )
                )

        for memory_event in _checkpoint_world_events(snapshot):
            if memory_event.step not in resolved_steps:
                event_sections.append(
                    (
                        memory_event.step,
                        f"## Step {memory_event.step}\n\n{memory_event.text}\n",
                    )
                )
            if memory_event.record_id not in projected_event_ids:
                timeline_lines.append(
                    (
                        memory_event.step,
                        f"- Step {memory_event.step} · `{memory_event.record_id}` · "
                        f"{memory_event.text}",
                    )
                )

        event_sections.sort(key=lambda item: item[0])
        timeline_lines.sort(key=lambda item: item[0])

        world = (
            f"# World · {target_branch_id}\n\n"
            f"Checkpoint: `{snapshot.checkpoint_id or 'uncommitted'}`  \n"
            f"State hash: `{snapshot.state_hash}`  \n"
            f"Step: {snapshot.current_step}\n\n"
            + (
                "\n".join(section for _, section in event_sections)
                if event_sections
                else "_No resolved events._\n"
            )
        )
        timeline = (
            f"# Timeline · {target_branch_id}\n\n"
            + (
                "\n".join(line for _, line in timeline_lines)
                if timeline_lines
                else "_No timeline entries._"
            )
            + "\n"
        )
        character_sections = []
        for actor_id, state in sorted(snapshot.actor_states.items()):
            memory = snapshot.memory_snapshots.get(actor_id)
            memory_count = memory.record_count if memory is not None else 0
            character_sections.append(
                f"## {actor_id}\n\n"
                f"Memory records: {memory_count}\n\n"
                f"```json\n{_json_block(state)}\n```\n"
            )
        characters = f"# Characters · {target_branch_id}\n\n" + (
            "\n".join(character_sections) if character_sections else "_No actors._\n"
        )

        batch = AtomicBatch(self.root)
        for path, content in (
            (world_path, world),
            (timeline_path, timeline),
            (characters_path, characters),
        ):
            batch.add(path.as_posix(), content)
        batch.commit()
        relative_paths = (world_path, timeline_path, characters_path)
        return tuple(self.root / path for path in relative_paths)
