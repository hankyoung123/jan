import json
from datetime import UTC, datetime
from pathlib import Path

from story_engine.domain.projection import ResolvedEvent
from story_engine.domain.simulation import TurnSessionSnapshot
from story_engine.domain.world_bible import (
    DirectorInstruction,
    WorldBibleCategory,
    WorldBibleEntry,
    WorldBibleSnapshot,
)
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.simulation_log import (
    SimulationLogRecord,
    SimulationLogStore,
)
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.project_store import ProjectSnapshot, ProjectStore


def read_branch_records(
    root: Path,
    branch_id: str,
) -> tuple[SimulationLogRecord, ...]:
    """Read the selected branch plus only the history inherited at each fork."""

    branches = BranchStore(root)
    checkpoints = CheckpointStore(root)
    logs = SimulationLogStore(root)

    def visit(selected_branch_id: str) -> tuple[SimulationLogRecord, ...]:
        branch = branches.load(selected_branch_id)
        inherited: tuple[SimulationLogRecord, ...] = ()
        if branch.parent_branch_id and branch.fork_checkpoint_id:
            fork = checkpoints.load(branch.fork_checkpoint_id)
            inherited = tuple(
                record
                for record in visit(branch.parent_branch_id)
                if record.result.step < fork.current_step
            )
        combined = (*inherited, *logs.read(selected_branch_id))
        by_trace = {record.trace.trace_id: record for record in combined}
        return tuple(
            sorted(
                by_trace.values(),
                key=lambda item: (item.result.step, item.trace.started_at),
            )
        )

    return visit(branch_id)


class WorldBibleStore:
    def __init__(self, root: Path, branch_id: str) -> None:
        self.root = root
        self.branch_id = branch_id
        self.directory = root / ".story-engine/projections" / branch_id
        self.path = self.directory / "world-bible.json"

    def load(self) -> WorldBibleSnapshot:
        return WorldBibleSnapshot.model_validate_json(
            self.path.read_text(encoding="utf-8")
        )

    def save(self, snapshot: WorldBibleSnapshot) -> tuple[Path, ...]:
        if snapshot.branch_id != self.branch_id:
            raise ValueError("world bible belongs to another branch")
        world_path = self.directory / "world.md"
        timeline_path = self.directory / "timeline.md"
        characters_path = self.directory / "characters.md"
        atomic_write_text(
            self.path,
            f"{snapshot.model_dump_json(indent=2)}\n",
        )
        atomic_write_text(world_path, self._render_world(snapshot))
        atomic_write_text(timeline_path, self._render_timeline(snapshot))
        atomic_write_text(characters_path, self._render_characters())
        return (self.path, world_path, timeline_path, characters_path)

    @staticmethod
    def _entry(entry: WorldBibleEntry) -> str:
        sources = ", ".join(entry.source_event_ids or entry.source_record_ids)
        return (
            f"### {entry.title}\n\n{entry.content_text}\n\n"
            f"- Status: {entry.status}\n"
            f"- Steps: {entry.first_seen_step}-{entry.last_updated_step}\n"
            f"- Sources: {sources or 'project seed'}\n"
        )

    @classmethod
    def _render_world(cls, snapshot: WorldBibleSnapshot) -> str:
        sections = [
            f"# World Bible · {snapshot.branch_id}",
            "## Creative Direction\n\n" + snapshot.creative_direction_text,
            "## Current World State\n\n" + snapshot.current_world_state_text,
        ]
        for title, entries in (
            ("Rules", snapshot.rules),
            ("Locations", snapshot.locations),
            ("Organizations", snapshot.organizations),
            ("Established Facts", snapshot.established_facts),
            ("Unresolved Threads", snapshot.unresolved_threads),
        ):
            sections.append(
                f"## {title}\n\n"
                + ("\n".join(cls._entry(item) for item in entries) or "_None._\n")
            )
        return "\n\n".join(sections).rstrip() + "\n"

    @classmethod
    def _render_timeline(cls, snapshot: WorldBibleSnapshot) -> str:
        content = "\n".join(cls._entry(item) for item in snapshot.history)
        return f"# Timeline · {snapshot.branch_id}\n\n{content or '_None._'}\n"

    def _render_characters(self) -> str:
        characters = ProjectStore(self.root).load().characters
        sections = [f"# Characters · {self.branch_id}"]
        for character in characters:
            sections.append(
                f"## {character.display_name or character.id}\n\n"
                f"- ID: `{character.id}`\n"
                f"- Type: {character.type}\n"
                f"- Identity: {character.identity}\n"
                f"- Core desire: {character.core_desire}\n"
                f"- Current goal: {character.current_goal or 'None'}\n"
                f"- Location: {character.location or 'Unknown'}"
            )
        return "\n\n".join(sections).rstrip() + "\n"

    def _instructions_path(self) -> Path:
        return self.directory / "director-instructions.json"

    def list_instructions(self) -> tuple[DirectorInstruction, ...]:
        path = self._instructions_path()
        if not path.exists():
            return ()
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(values, list):
                raise ValueError
            return tuple(DirectorInstruction.model_validate(item) for item in values)
        except (OSError, ValueError) as error:
            raise ValueError("director instructions are invalid") from error

    def add_instruction(self, instruction: DirectorInstruction) -> Path:
        current = self.list_instructions()
        if any(item.instruction_id == instruction.instruction_id for item in current):
            raise ValueError("director instruction already exists")
        updated = (*current, instruction)
        path = self._instructions_path()
        atomic_write_text(
            path,
            json.dumps(
                [item.model_dump(mode="json") for item in updated],
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        notes = self.directory / "director-notes.md"
        atomic_write_text(
            notes,
            "# Director Notes\n\n"
            + "\n\n".join(
                f"## {item.instruction_id}\n\n{item.text}\n\n"
                f"Applies from: `{item.applies_from_checkpoint_id}`"
                for item in updated
            )
            + "\n",
        )
        return notes


class WorldBibleProjector:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def from_project_seed(
        project: ProjectSnapshot,
        *,
        branch_id: str = "main",
    ) -> WorldBibleSnapshot:
        source_id = f"submission:{project.project.id}"
        rules = tuple(
            WorldBibleEntry(
                entry_id=f"world-rule:{index}",
                category=WorldBibleCategory.RULE,
                title=f"Rule {index}",
                content_text=text,
                source_record_ids=(source_id,),
                source_event_ids=(),
                first_seen_step=0,
                last_updated_step=0,
                confidence=1,
                status="active",
            )
            for index, text in enumerate(project.world.rules, start=1)
        )
        locations = []
        for index, name in enumerate(
            dict.fromkeys(
                value
                for value in (
                    project.world.current_location,
                    *(item.location for item in project.characters),
                )
                if value
            ),
            start=1,
        ):
            locations.append(
                WorldBibleEntry(
                    entry_id=f"world-location:{index}",
                    category=WorldBibleCategory.LOCATION,
                    title=name,
                    content_text=name,
                    source_record_ids=(source_id,),
                    source_event_ids=(),
                    first_seen_step=0,
                    last_updated_step=0,
                    confidence=1,
                    status="active",
                )
            )
        facts = tuple(
            WorldBibleEntry(
                entry_id=f"world-fact:{index}",
                category=WorldBibleCategory.ESTABLISHED_FACT,
                title=fact.id,
                content_text=fact.statement,
                source_record_ids=(fact.source_event_id,),
                source_event_ids=(),
                first_seen_step=0,
                last_updated_step=0,
                confidence=1,
                status="active",
            )
            for index, fact in enumerate(project.facts, start=1)
        )
        incident = project.world.world_variables.get("initial_incident")
        history = (
            WorldBibleEntry(
                entry_id="world-history:initial",
                category=WorldBibleCategory.HISTORY,
                title="Initial incident",
                content_text=str(incident),
                source_record_ids=(source_id,),
                source_event_ids=(),
                first_seen_step=0,
                last_updated_step=0,
                confidence=1,
                status="historical",
            ),
        ) if incident else ()
        threads = tuple(
            WorldBibleEntry(
                entry_id=f"world-thread:{index}",
                category=WorldBibleCategory.UNRESOLVED_THREAD,
                title=f"Pressure {index}",
                content_text=text,
                source_record_ids=(source_id,),
                source_event_ids=(),
                first_seen_step=0,
                last_updated_step=0,
                confidence=1,
                status="active",
            )
            for index, text in enumerate(project.world.active_pressures, start=1)
        )
        return WorldBibleSnapshot(
            project_id=project.project.id,
            branch_id=branch_id,
            checkpoint_id=f"seed:{project.project.id}",
            creative_direction_text=(
                f"{project.project.title} · {project.project.genre}\n"
                f"Theme: {project.project.theme}\nTone: {project.project.tone}"
            ),
            current_world_state_text=(
                f"Time: {project.world.current_time}\n"
                f"Location: {project.world.current_location or 'Unknown'}"
            ),
            rules=rules,
            locations=tuple(locations),
            organizations=(),
            history=history,
            established_facts=facts,
            unresolved_threads=threads,
            generated_at=datetime.now(UTC),
        )

    def initialize(self, project: ProjectSnapshot) -> WorldBibleSnapshot:
        snapshot = self.from_project_seed(project)
        WorldBibleStore(self.root, "main").save(snapshot)
        return snapshot

    async def rebuild(
        self,
        snapshot: TurnSessionSnapshot,
        records: tuple[SimulationLogRecord, ...],
        *,
        previous: WorldBibleSnapshot | None,
    ) -> WorldBibleSnapshot:
        return self.rebuild_sync(snapshot, records, previous=previous)

    def rebuild_sync(
        self,
        snapshot: TurnSessionSnapshot,
        records: tuple[SimulationLogRecord, ...],
        *,
        previous: WorldBibleSnapshot | None,
    ) -> WorldBibleSnapshot:
        base = previous or self.from_project_seed(
            ProjectStore(self.root).load(),
            branch_id=snapshot.branch_id,
        )
        selected_records = tuple(
            record
            for record in records
            if record.result.step < snapshot.current_step
            and record.result.resolved_turn is not None
        )
        events = tuple(
            event
            for record in selected_records
            if record.result.resolved_turn is not None
            for event in record.result.resolved_turn.events
        )
        history_by_id = {item.entry_id: item for item in base.history}
        facts_by_id = {item.entry_id: item for item in base.established_facts}
        locations_by_title = {item.title: item for item in base.locations}
        for event in events:
            history_entry, fact_entry = self._entries_from_event(event)
            history_by_id[history_entry.entry_id] = history_entry
            facts_by_id[fact_entry.entry_id] = fact_entry
            for location in event.location_ids:
                previous_location = locations_by_title.get(location)
                locations_by_title[location] = WorldBibleEntry(
                    entry_id=(
                        previous_location.entry_id
                        if previous_location is not None
                        else f"world-location:{len(locations_by_title) + 1}"
                    ),
                    category=WorldBibleCategory.LOCATION,
                    title=location,
                    content_text=location,
                    source_record_ids=event.source_memory_ids,
                    source_event_ids=(event.event_id,),
                    first_seen_step=(
                        previous_location.first_seen_step
                        if previous_location is not None
                        else event.step
                    ),
                    last_updated_step=event.step,
                    confidence=event.confidence,
                    status="active",
                )
        latest = events[-1].event_text if events else base.current_world_state_text
        rebuilt = WorldBibleSnapshot(
            project_id=snapshot.project_id,
            branch_id=snapshot.branch_id,
            checkpoint_id=snapshot.checkpoint_id
            or f"checkpoint:{snapshot.state_hash}",
            creative_direction_text=base.creative_direction_text,
            current_world_state_text=latest,
            rules=base.rules,
            locations=tuple(locations_by_title.values()),
            organizations=base.organizations,
            history=tuple(history_by_id.values()),
            established_facts=tuple(facts_by_id.values()),
            unresolved_threads=base.unresolved_threads,
            generated_at=datetime.now(UTC),
        )
        WorldBibleStore(self.root, snapshot.branch_id).save(rebuilt)
        return rebuilt

    @staticmethod
    def _entries_from_event(
        event: ResolvedEvent,
    ) -> tuple[WorldBibleEntry, WorldBibleEntry]:
        suffix = event.event_id.removeprefix("event:")
        title = event.event_text.splitlines()[0][:160]
        return (
            WorldBibleEntry(
                entry_id=f"world-history:{suffix}",
                category=WorldBibleCategory.HISTORY,
                title=title,
                content_text=event.event_text,
                source_record_ids=event.source_memory_ids,
                source_event_ids=(event.event_id,),
                first_seen_step=event.step,
                last_updated_step=event.step,
                confidence=event.confidence,
                status="historical",
            ),
            WorldBibleEntry(
                entry_id=f"world-fact:{suffix}",
                category=WorldBibleCategory.ESTABLISHED_FACT,
                title=title,
                content_text=event.event_text,
                source_record_ids=event.source_memory_ids,
                source_event_ids=(event.event_id,),
                first_seen_step=event.step,
                last_updated_step=event.step,
                confidence=event.confidence,
                status="active",
            ),
        )


class WorldBibleService:
    def __init__(self, root: Path, branch_id: str) -> None:
        self.root = root
        self.branch_id = branch_id
        self.store = WorldBibleStore(root, branch_id)
        self.projector = WorldBibleProjector(root)
        self.branches = BranchStore(root)
        self.checkpoints = CheckpointStore(root)

    def get(self, checkpoint_id: str | None = None) -> WorldBibleSnapshot:
        try:
            branch = self.branches.load(self.branch_id)
        except FileNotFoundError:
            if self.branch_id != "main":
                raise
            return self.store.load()
        selected = checkpoint_id or branch.head_checkpoint_id
        try:
            current = self.store.load()
            if selected is None or current.checkpoint_id == selected:
                return current
        except FileNotFoundError:
            current = None
        if selected is None:
            return self.projector.initialize(ProjectStore(self.root).load())
        snapshot = self.checkpoints.load(selected)
        return self.projector.rebuild_sync(
            snapshot,
            read_branch_records(self.root, self.branch_id),
            previous=None,
        )

    def rebuild(self, checkpoint_id: str | None = None) -> WorldBibleSnapshot:
        branch = self.branches.load(self.branch_id)
        selected = checkpoint_id or branch.head_checkpoint_id
        if selected is None:
            raise ValueError("branch has no checkpoint")
        snapshot = self.checkpoints.load(selected)
        return self.projector.rebuild_sync(
            snapshot,
            read_branch_records(self.root, self.branch_id),
            previous=None,
        )
