from typing import Self

from pydantic import Field, JsonValue, model_validator

from story_engine.domain.action import EntityRole
from story_engine.domain.base import Identifier, LocaleCode, RuntimeModel


class EntityPerception(RuntimeModel):
    entity_id: Identifier
    display_name: str
    description_text: str
    relationship_text: str | None = None


class PerceptionFrame(RuntimeModel):
    frame_id: Identifier
    session_id: Identifier
    branch_id: Identifier
    actor_id: Identifier
    step: int = Field(ge=0)
    content_locale: LocaleCode
    observation_text: str = Field(min_length=1, max_length=65_536)
    visible_entities: tuple[EntityPerception, ...] = ()
    participant_ids: tuple[Identifier, ...] = ()
    source_record_ids: tuple[Identifier, ...] = ()
    location_ids: tuple[Identifier, ...] = ()
    generated_by_gm: bool = True


class ComponentRecipe(RuntimeModel):
    component_id: Identifier
    component_type: Identifier
    enabled: bool = True
    order: int = Field(ge=0)
    params: dict[str, JsonValue] = Field(default_factory=dict)


class AgentRecipe(RuntimeModel):
    recipe_id: Identifier
    version: Identifier
    role: EntityRole
    prefab_type: Identifier
    model_profile_id: Identifier
    memory_profile_id: Identifier | None = None
    components: tuple[ComponentRecipe, ...]
    content_locale: LocaleCode
    system_instruction_text: str = Field(min_length=1, max_length=65_536)
    max_context_tokens: int = Field(default=32_768, ge=1_024)
    tags: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def component_ids_are_unique(self) -> Self:
        component_ids = [item.component_id for item in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("component IDs must be unique")
        orders = [item.order for item in self.components if item.enabled]
        if len(orders) != len(set(orders)):
            raise ValueError("enabled component orders must be unique")
        return self
