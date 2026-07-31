from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import frontmatter
from pydantic import Field

from story_engine.domain.models import (
    Character,
    CharacterType,
    DomainModel,
    Relationship,
    StateChange,
    StoryEvent,
    WorldState,
)


class ProjectDocument(DomainModel):
    schema_name: Literal["project/v1"] = Field(
        default="project/v1",
        serialization_alias="schema",
        validation_alias="schema",
    )
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    genre: str = Field(min_length=1)
    theme: str = Field(min_length=1)
    tone: str = Field(min_length=1)
    version: int = Field(default=0, ge=0)


class WorldDocument(DomainModel):
    schema_name: Literal["world/v1"] = Field(
        default="world/v1",
        serialization_alias="schema",
        validation_alias="schema",
    )
    current_time: str = Field(min_length=1)
    current_location: str | None = None
    rules: tuple[str, ...] = ()
    active_pressures: tuple[str, ...] = ()
    public_fact_ids: tuple[str, ...] = ()
    world_variables: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict
    )
    version: int = Field(default=0, ge=0)

    @classmethod
    def from_domain(cls, world: WorldState) -> "WorldDocument":
        return cls.model_validate(world.model_dump())

    def to_domain(self) -> WorldState:
        return WorldState.model_validate(
            self.model_dump(exclude={"schema_name"}),
        )


class CharacterDocument(DomainModel):
    schema_name: Literal["character/v1"] = Field(
        default="character/v1",
        serialization_alias="schema",
        validation_alias="schema",
    )
    id: str = Field(min_length=1)
    display_name: str | None = None
    type: CharacterType
    identity: str = Field(min_length=1)
    core_desire: str = Field(min_length=1)
    current_goal: str | None = None
    known_fact_ids: tuple[str, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    location: str | None = None
    emotional_state: str | None = None
    resources: tuple[str, ...] = ()
    last_event_id: str | None = None
    version: int = Field(default=0, ge=0)

    @classmethod
    def from_domain(cls, character: Character) -> "CharacterDocument":
        return cls.model_validate(character.model_dump())

    def to_domain(self) -> Character:
        return Character.model_validate(
            self.model_dump(exclude={"schema_name"}),
        )


class EventDocument(DomainModel):
    schema_name: Literal["story-event/v1"] = Field(
        default="story-event/v1",
        serialization_alias="schema",
        validation_alias="schema",
    )
    id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    occurred_at: datetime
    summary: str = Field(min_length=1)
    participants: tuple[str, ...]
    public_results: tuple[str, ...] = ()
    hidden_results: tuple[str, ...] = ()
    character_changes: tuple[StateChange, ...] = ()
    world_changes: tuple[StateChange, ...] = ()
    source_turn_id: str = Field(min_length=1)
    approved_by_user: bool

    @classmethod
    def from_domain(cls, event: StoryEvent) -> "EventDocument":
        return cls.model_validate(event.model_dump())

    def to_domain(self) -> StoryEvent:
        return StoryEvent.model_validate(
            self.model_dump(exclude={"schema_name"}),
        )


def dump_document(document: DomainModel, body: str) -> str:
    metadata = document.model_dump(mode="json", by_alias=True)
    post = frontmatter.Post(body.rstrip(), **metadata)
    return f"{frontmatter.dumps(post, sort_keys=False).rstrip()}\n"


def load_document[DocumentT: DomainModel](
    path: Path,
    model: type[DocumentT],
) -> tuple[DocumentT, str]:
    post = frontmatter.load(path)
    metadata: dict[str, Any] = dict(post.metadata)
    return model.model_validate(metadata), str(post.content)


def render_project(document: ProjectDocument) -> str:
    body = f"""# {document.title}

## Creative Direction

- Genre: {document.genre}
- Theme: {document.theme}
- Tone: {document.tone}
"""
    return dump_document(document, body)


def render_world(world: WorldState) -> str:
    document = WorldDocument.from_domain(world)
    rules = "\n".join(f"- {item}" for item in world.rules) or "- None"
    pressures = "\n".join(f"- {item}" for item in world.active_pressures) or "- None"
    facts = "\n".join(f"- {item}" for item in world.public_fact_ids) or "- None"
    variables = (
        "\n".join(f"- {key}: {value}" for key, value in world.world_variables.items())
        or "- None"
    )
    body = f"""# World

## Current State

- Time: {world.current_time}
- Location: {world.current_location or "Unknown"}

## Rules

{rules}

## Active Pressures

{pressures}

## Public Facts

{facts}

## Variables

{variables}
"""
    return dump_document(document, body)


def render_character(character: Character) -> str:
    document = CharacterDocument.from_domain(character)
    facts = "\n".join(f"- {item}" for item in character.known_fact_ids) or "- None"
    relationships = (
        "\n".join(
            f"- {relationship.character_id}: {relationship.description}"
            for relationship in character.relationships
        )
        or "- None"
    )
    resources = "\n".join(f"- {item}" for item in character.resources) or "- None"
    body = f"""# {character.display_name or character.id}

## Identity

{character.identity}

## Core Desire

{character.core_desire}

## Current Goal

{character.current_goal or "None"}

## Known Facts

{facts}

## Relationships

{relationships}

## Current State

- Location: {character.location or "Unknown"}
- Emotion: {character.emotional_state or "Unknown"}
- Resources:
{resources}
"""
    return dump_document(document, body)


def render_event(event: StoryEvent) -> str:
    document = EventDocument.from_domain(event)
    public = "\n".join(f"- {item}" for item in event.public_results) or "- None"
    hidden = "\n".join(f"- {item}" for item in event.hidden_results) or "- None"
    body = f"""# Event {event.sequence:06d}

{event.summary}

## Public Results

{public}

## Hidden Results

{hidden}
"""
    return dump_document(document, body)
