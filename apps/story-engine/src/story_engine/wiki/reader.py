import json
from pathlib import Path

from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.projection import EventVisibility
from story_engine.domain.simulation import TurnSessionSnapshot
from story_engine.domain.wiki import WikiSource, WikiSourceKind
from story_engine.persistence.simulation_log import SimulationLogRecord
from story_engine.wiki.store import WikiStore
from story_engine.workspace.project_store import ProjectStore


def decode_snapshot_memories(
    snapshot: TurnSessionSnapshot,
) -> tuple[MemoryRecord, ...]:
    records: list[MemoryRecord] = []
    for memory_snapshot in snapshot.memory_snapshots.values():
        bank = ConcordiaMemoryBank(
            owner_id=memory_snapshot.owner_id,
            scope=memory_snapshot.scope,
        )
        bank.restore(memory_snapshot)
        records.extend(bank.scan(lambda _record: True))
    return tuple(records)


class WikiSourceReader:
    """Expose raw runtime sources with a strict world/character privacy boundary."""

    def __init__(self, root: Path, branch_id: str) -> None:
        self.root = root
        self.branch_id = branch_id

    def project_sources(self) -> tuple[WikiSource, ...]:
        snapshot = ProjectStore(self.root).load()
        sources = [
            WikiSource(
                source_id=f"project:{snapshot.project.id}",
                kind=WikiSourceKind.PROJECT,
                branch_id=self.branch_id,
                step=0,
                content=json.dumps(
                    {
                        "project": snapshot.project.model_dump(mode="json"),
                        "world": snapshot.world.model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                ),
            )
        ]
        for character in snapshot.characters:
            sources.append(
                WikiSource(
                    source_id=f"profile:{character.id}",
                    kind=WikiSourceKind.PROFILE,
                    branch_id=self.branch_id,
                    subject_id=character.id,
                    step=0,
                    content=json.dumps(
                        character.model_dump(mode="json"),
                        ensure_ascii=False,
                    ),
                )
            )
        for fact in snapshot.facts:
            subjects = fact.known_by or (None,)
            for subject in subjects:
                sources.append(
                    WikiSource(
                        source_id=fact.id,
                        kind=(
                            WikiSourceKind.PROJECT
                            if subject is None
                            else WikiSourceKind.PROFILE
                        ),
                        branch_id=self.branch_id,
                        subject_id=subject,
                        step=0,
                        content=fact.statement,
                    )
                )
        return tuple(sources)

    def world_sources(
        self,
        *,
        records: tuple[SimulationLogRecord, ...],
        snapshot: TurnSessionSnapshot,
    ) -> tuple[WikiSource, ...]:
        sources = [
            source
            for source in self.project_sources()
            if source.kind == WikiSourceKind.PROJECT
        ]
        for record in records:
            if record.result.resolved_turn is None:
                continue
            sources.extend(
                WikiSource(
                    source_id=event.event_id,
                    kind=WikiSourceKind.EVENT,
                    branch_id=self.branch_id,
                    step=event.step,
                    content=event.event_text,
                )
                for event in record.result.resolved_turn.events
            )
        sources.extend(
            WikiSource(
                source_id=memory.record_id,
                kind=WikiSourceKind.GM_MEMORY,
                branch_id=self.branch_id,
                step=memory.step,
                content=memory.text,
            )
            for memory in decode_snapshot_memories(snapshot)
            if memory.scope == MemoryScope.GAME_MASTER
            and memory.record_type
            in {
                MemoryRecordType.PREMISE,
                MemoryRecordType.WORLD_EVENT,
                MemoryRecordType.SYSTEM,
            }
        )
        sources.extend(
            WikiSource(
                source_id=instruction.instruction_id,
                kind=WikiSourceKind.DIRECTOR_INSTRUCTION,
                branch_id=self.branch_id,
                step=snapshot.current_step,
                content=instruction.text,
            )
            for instruction in WikiStore(self.root, self.branch_id).list_instructions()
        )
        return tuple({source.source_id: source for source in sources}.values())

    def character_sources(
        self,
        subject_id: str,
        *,
        records: tuple[SimulationLogRecord, ...],
        snapshot: TurnSessionSnapshot,
    ) -> tuple[WikiSource, ...]:
        sources = [
            source
            for source in self.project_sources()
            if source.subject_id == subject_id
        ]
        for record in records:
            result = record.result
            if result.acting_actor_id == subject_id and result.action_text:
                sources.append(
                    WikiSource(
                        source_id=f"action:{result.session_id}:{result.step}",
                        kind=WikiSourceKind.ACTION,
                        branch_id=self.branch_id,
                        subject_id=subject_id,
                        step=result.step,
                        content=result.action_text,
                    )
                )
            if result.resolved_turn is None:
                continue
            for event in result.resolved_turn.events:
                visible = (
                    event.visibility == EventVisibility.PUBLIC
                    or subject_id in event.participant_ids
                    or subject_id in event.observer_ids
                )
                if visible:
                    # A character consumes the private observation derived from this
                    # event, never the GM-only event source itself.
                    continue
        sources.extend(
            WikiSource(
                source_id=memory.record_id,
                kind=WikiSourceKind.OBSERVATION,
                branch_id=self.branch_id,
                subject_id=subject_id,
                step=memory.step,
                content=memory.text,
            )
            for memory in decode_snapshot_memories(snapshot)
            if memory.scope == MemoryScope.CHARACTER
            and memory.owner_id == subject_id
            and memory.record_type
            in {MemoryRecordType.PREMISE, MemoryRecordType.OBSERVATION}
        )
        return tuple({source.source_id: source for source in sources}.values())
