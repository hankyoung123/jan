import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import frontmatter
from pydantic import Field

from story_engine.domain.models import (
    Character,
    CharacterType,
    DomainModel,
    Fact,
    FactVisibility,
    Relationship,
    WorldState,
)
from story_engine.manuscript.models import Scene

_JSON_PAYLOAD = re.compile(r"```json\n(?P<payload>.*?)\n```", re.DOTALL)


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
    version: int = Field(default=0, ge=0)

    @classmethod
    def from_domain(cls, character: Character) -> "CharacterDocument":
        return cls.model_validate(character.model_dump())

    def to_domain(self) -> Character:
        return Character.model_validate(
            self.model_dump(exclude={"schema_name"}),
        )


class FactDocument(DomainModel):
    schema_name: Literal["fact/v1"] = Field(
        default="fact/v1",
        serialization_alias="schema",
        validation_alias="schema",
    )
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    visibility: FactVisibility
    known_by: tuple[str, ...] = ()
    source_event_id: str = Field(min_length=1)
    introduced_at: datetime
    supersedes_fact_id: str | None = None

    @classmethod
    def from_domain(cls, fact: Fact) -> "FactDocument":
        return cls.model_validate(fact.model_dump())

    def to_domain(self) -> Fact:
        return Fact.model_validate(self.model_dump(exclude={"schema_name"}))


class SceneDocument(DomainModel):
    schema_name: Literal["scene/v2"] = Field(
        default="scene/v2",
        serialization_alias="schema",
        validation_alias="schema",
    )
    id: str = Field(pattern=r"^scene-[0-9]{6}$")
    project_id: str = Field(min_length=1)
    branch_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    chapter_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_event_ids: tuple[str, ...] = Field(min_length=1)
    source_checkpoint_id: str = Field(min_length=1)
    source_from_step: int = Field(ge=0)
    source_to_step: int = Field(ge=0)
    source_memory_ids: tuple[str, ...] = ()
    source_wiki_branch_id: str = Field(min_length=1)
    source_wiki_version_id: str = Field(min_length=1)
    viewpoint_actor_id: str | None = None
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, scene: Scene) -> "SceneDocument":
        return cls.model_validate(scene.model_dump(exclude={"body"}))

    def to_domain(self, body: str) -> Scene:
        return Scene.model_validate(
            {**self.model_dump(exclude={"schema_name"}), "body": body}
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


def dump_json_envelope(
    *,
    schema: str,
    title: str,
    payload: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
    body: str | None = None,
) -> str:
    """Render exact structured state inside an inspectable Markdown document."""
    front_matter = {"schema": schema, **dict(metadata or {})}
    structured = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    content = body or f"# {title}"
    post = frontmatter.Post(
        f"{content.rstrip()}\n\n## Structured Payload\n\n```json\n{structured}\n```",
        **front_matter,
    )
    return f"{frontmatter.dumps(post, sort_keys=False).rstrip()}\n"


def load_json_envelope(path: Path, *, schema: str) -> dict[str, Any]:
    try:
        post = frontmatter.load(path)
        if post.metadata.get("schema") != schema:
            raise ValueError("Markdown envelope schema mismatch")
        matches = tuple(_JSON_PAYLOAD.finditer(str(post.content)))
        if not matches:
            raise ValueError("Markdown envelope has no JSON payload")
        payload = json.loads(matches[-1].group("payload"))
    except FileNotFoundError:
        raise
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"Markdown envelope {path.name!r} is invalid") from error
    if not isinstance(payload, dict):
        raise ValueError("Markdown envelope payload must be an object")
    return payload


def render_project(document: ProjectDocument) -> str:
    body = f"""# {document.title}

## Creative Direction

- Genre: {document.genre}
- Theme: {document.theme}
- Tone: {document.tone}
"""
    return dump_document(document, body)


def render_world(world: WorldState, facts: tuple[Fact, ...]) -> str:
    document = WorldDocument.from_domain(world)
    rules = "\n".join(f"- {item}" for item in world.rules) or "- None"
    pressures = "\n".join(f"- {item}" for item in world.active_pressures) or "- None"
    facts_by_id = {fact.id: fact for fact in facts}
    public_facts = (
        "\n".join(
            f"- [{fact_id}] {facts_by_id[fact_id].statement}"
            for fact_id in world.public_fact_ids
            if fact_id in facts_by_id
        )
        or "- None"
    )
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

{public_facts}

## Variables

{variables}
"""
    return dump_document(document, body)


def render_character(character: Character, facts: tuple[Fact, ...]) -> str:
    document = CharacterDocument.from_domain(character)
    facts_by_id = {fact.id: fact for fact in facts}
    known_facts = (
        "\n".join(
            f"- [{fact_id}] {facts_by_id[fact_id].statement}"
            for fact_id in character.known_fact_ids
            if fact_id in facts_by_id
        )
        or "- None"
    )
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

{known_facts}

## Relationships

{relationships}

## Current State

- Location: {character.location or "Unknown"}
- Emotion: {character.emotional_state or "Unknown"}
- Resources:
{resources}
"""
    return dump_document(document, body)


def render_fact(fact: Fact) -> str:
    document = FactDocument.from_domain(fact)
    body = f"# Fact\n\n{fact.statement}\n"
    return dump_document(document, body)


def render_scene(scene: Scene) -> str:
    document = SceneDocument.from_domain(scene)
    return dump_document(document, scene.body)
