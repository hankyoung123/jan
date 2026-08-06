from typing import Literal

from pydantic import Field, model_validator

from story_engine.domain.base import Identifier, LocaleCode, RuntimeModel
from story_engine.domain.memory import MemoryRecord
from story_engine.domain.projection import ResolvedEvent, SimulationBoundary
from story_engine.domain.wiki import WikiContextManifestEntry

NarrativeSourceStatus = Literal[
    "available",
    "drafted",
    "needs_revision",
    "saved",
]


class NarrativeSource(RuntimeModel):
    project_id: Identifier
    branch_id: Identifier
    checkpoint_id: Identifier
    from_step: int = Field(ge=0)
    to_step: int = Field(ge=0)
    boundary: SimulationBoundary
    event_ids: tuple[Identifier, ...]
    memory_record_ids: tuple[Identifier, ...]
    wiki_branch_id: Identifier
    wiki_version_id: str = Field(min_length=1)
    viewpoint_actor_id: Identifier | None = None
    content_locale: LocaleCode

    @model_validator(mode="after")
    def step_range_is_ordered(self) -> "NarrativeSource":
        if self.to_step < self.from_step:
            raise ValueError("narrative source step range is reversed")
        if len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("narrative source event ids must be unique")
        if len(self.memory_record_ids) != len(set(self.memory_record_ids)):
            raise ValueError("narrative source memory ids must be unique")
        return self


class NarrativeSourceSummary(RuntimeModel):
    source_id: Identifier
    branch_id: Identifier
    checkpoint_id: Identifier
    from_step: int = Field(ge=0)
    to_step: int = Field(ge=0)
    boundary: SimulationBoundary
    title_hint: str = Field(min_length=1, max_length=512)
    event_summary_text: str = Field(min_length=1, max_length=32_768)
    available_viewpoint_ids: tuple[Identifier, ...]
    status: NarrativeSourceStatus


class WriterContext(RuntimeModel):
    source: NarrativeSource
    events: tuple[ResolvedEvent, ...]
    viewpoint_memories: tuple[MemoryRecord, ...] = ()
    world_wiki_context: str = ""
    wiki_context_manifest: tuple[WikiContextManifestEntry, ...] = ()

    @model_validator(mode="after")
    def context_stays_within_source(self) -> "WriterContext":
        if not {event.event_id for event in self.events}.issubset(
            set(self.source.event_ids)
        ):
            raise ValueError("Writer events exceed the narrative source")
        if not {record.record_id for record in self.viewpoint_memories}.issubset(
            set(self.source.memory_record_ids)
        ):
            raise ValueError("Writer memories exceed the narrative source")
        return self


class EditorContext(RuntimeModel):
    source: NarrativeSource
    events: tuple[ResolvedEvent, ...]
    memories: tuple[MemoryRecord, ...]
    world_wiki_context: str = ""
    wiki_context_manifest: tuple[WikiContextManifestEntry, ...] = ()

    @model_validator(mode="after")
    def source_ids_match_loaded_context(self) -> "EditorContext":
        if tuple(event.event_id for event in self.events) != self.source.event_ids:
            raise ValueError("Editor events do not match the narrative source")
        if tuple(record.record_id for record in self.memories) != (
            self.source.memory_record_ids
        ):
            raise ValueError("Editor memories do not match the narrative source")
        return self
