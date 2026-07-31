from pydantic import Field

from story_engine.domain.models import Character, DomainModel, WorldState
from story_engine.workspace.project_store import ProjectSnapshot


class CharacterContext(DomainModel):
    character: Character
    world: WorldState
    visible_fact_ids: tuple[str, ...] = Field(min_length=1)


class CharacterContextAssembler:
    def assemble(self, snapshot: ProjectSnapshot) -> dict[str, CharacterContext]:
        public = set(snapshot.world.public_fact_ids)
        return {
            character.id: CharacterContext(
                character=character,
                world=snapshot.world,
                visible_fact_ids=tuple(sorted(public | set(character.known_fact_ids))),
            )
            for character in snapshot.characters
            if character.type == "active"
        }
