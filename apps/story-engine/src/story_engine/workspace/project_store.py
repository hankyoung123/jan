import json
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from story_engine.domain.models import Character, DomainModel, Fact, WorldState
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import (
    CharacterDocument,
    ProjectDocument,
    WorldDocument,
    load_document,
    render_character,
    render_project,
    render_world,
)
from story_engine.workspace.fact_store import FactStore
from story_engine.workspace.lock import ProjectLock


class ProjectSeed(DomainModel):
    id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    genre: str = Field(min_length=1)
    theme: str = Field(min_length=1)
    tone: str = Field(min_length=1)
    world: WorldState
    characters: tuple[Character, ...] = Field(min_length=1, max_length=4)
    facts: tuple[Fact, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def contains_active_character(self) -> Self:
        if not any(character.type == "active" for character in self.characters):
            raise ValueError("project requires at least one active character")
        ids = [character.id for character in self.characters]
        if len(ids) != len(set(ids)):
            raise ValueError("character ids must be unique")
        fact_ids = [fact.id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("fact ids must be unique")
        known_fact_ids = {
            fact_id
            for character in self.characters
            for fact_id in character.known_fact_ids
        }
        public_fact_ids = set(self.world.public_fact_ids)
        if known_fact_ids | public_fact_ids != set(fact_ids):
            raise ValueError("seed fact registry must match world and character facts")
        characters_by_id = {character.id for character in self.characters}
        for fact in self.facts:
            expected_public = fact.id in public_fact_ids
            if expected_public != (fact.visibility == "public"):
                raise ValueError("fact visibility must match world public facts")
            if not set(fact.known_by).issubset(characters_by_id):
                raise ValueError("fact known_by contains an unknown character")
            actual_owners = {
                character.id
                for character in self.characters
                if fact.id in character.known_fact_ids
            }
            if fact.visibility != "public" and actual_owners != set(fact.known_by):
                raise ValueError("character knowledge must match fact known_by")
        return self


class ProjectSnapshot(DomainModel):
    project: ProjectDocument
    world: WorldState
    characters: tuple[Character, ...]
    facts: tuple[Fact, ...]


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def create(self, seed: ProjectSeed) -> ProjectSnapshot:
        if (self.root / "project.md").exists():
            raise FileExistsError(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in (
            "characters/active",
            "characters/npc",
            "characters/retired",
            "facts",
            "sources",
            ".story-engine/manuscript",
            ".story-engine/runtime/sessions",
            ".story-engine/cache",
            ".story-engine/index",
            ".story-engine/recovery",
        ):
            (self.root / directory).mkdir(parents=True, exist_ok=True)

        project = ProjectDocument(
            id=seed.id,
            title=seed.title,
            genre=seed.genre,
            theme=seed.theme,
            tone=seed.tone,
        )
        atomic_write_text(
            self.root / "project.md",
            render_project(project),
            overwrite=False,
        )
        fact_store = FactStore(self.root)
        for fact in seed.facts:
            fact_store.save(fact)
        self.save_world(seed.world, overwrite=False)
        for character in seed.characters:
            self.save_character(character, overwrite=False)
        return self.load()

    def load(self) -> ProjectSnapshot:
        project, _ = load_document(self.root / "project.md", ProjectDocument)
        world_document, _ = load_document(self.root / "world.md", WorldDocument)
        characters: list[Character] = []
        for group in ("active", "npc", "retired"):
            for path in sorted((self.root / "characters" / group).glob("*.md")):
                document, _ = load_document(path, CharacterDocument)
                characters.append(document.to_domain())
        return ProjectSnapshot(
            project=project,
            world=world_document.to_domain(),
            characters=tuple(characters),
            facts=FactStore(self.root).list_facts(),
        )

    def save_world(self, world: WorldState, *, overwrite: bool = True) -> Path:
        with ProjectLock(self.root):
            path = self.root / "world.md"
            facts = FactStore(self.root).list_facts()
            atomic_write_text(path, render_world(world, facts), overwrite=overwrite)
            return path

    def save_character(
        self,
        character: Character,
        *,
        overwrite: bool = True,
    ) -> Path:
        with ProjectLock(self.root):
            path = self.character_path(character)
            facts = FactStore(self.root).list_facts()
            atomic_write_text(
                path,
                render_character(character, facts),
                overwrite=overwrite,
            )
            return path

    def character_path(self, character: Character) -> Path:
        return self.root / "characters" / character.type / f"{character.id}.md"

    def rebuild_index(self) -> Path:
        snapshot = self.load()
        data = {
            "project_id": snapshot.project.id,
            "world_version": snapshot.world.version,
            "characters": [character.id for character in snapshot.characters],
            "facts": [fact.id for fact in snapshot.facts],
        }
        path = self.root / ".story-engine/index/project.json"
        atomic_write_text(
            path,
            f"{json.dumps(data, ensure_ascii=False, indent=2)}\n",
        )
        return path
