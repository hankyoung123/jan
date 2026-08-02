from pydantic import Field

from story_engine.domain.models import Character, DomainModel, Fact
from story_engine.workspace.project_store import ProjectSnapshot


class CharacterPerception(DomainModel):
    current_time: str = Field(min_length=1)
    perceived_location: str | None = None
    perceived_pressures: tuple[str, ...] = ()
    visible_rules: tuple[str, ...] = ()
    visible_facts: tuple[Fact, ...] = Field(min_length=1)
    sensory_context: tuple[str, ...] = ()


class CharacterContext(DomainModel):
    character: Character
    perception: CharacterPerception


class CharacterContextAssembler:
    def assemble(self, snapshot: ProjectSnapshot) -> dict[str, CharacterContext]:
        public = set(snapshot.world.public_fact_ids)
        facts_by_id = {fact.id: fact for fact in snapshot.facts}
        return {
            character.id: CharacterContext(
                character=character,
                perception=CharacterPerception(
                    current_time=snapshot.world.current_time,
                    perceived_location=(
                        character.location or snapshot.world.current_location
                    ),
                    perceived_pressures=snapshot.world.active_pressures,
                    visible_rules=snapshot.world.rules,
                    visible_facts=tuple(
                        facts_by_id[fact_id]
                        for fact_id in sorted(
                            public | set(character.known_fact_ids)
                        )
                        if fact_id in facts_by_id
                    ),
                ),
            )
            for character in snapshot.characters
            if character.type == "active"
        }
