import dataclasses
from collections.abc import Mapping

from concordia.agents import entity_agent_with_logging  # type: ignore[import-untyped]
from concordia.associative_memory import (  # type: ignore[import-untyped]
    basic_associative_memory,
)
from concordia.components import (  # type: ignore[import-untyped]
    agent as agent_components,
)
from concordia.language_model import language_model  # type: ignore[import-untyped]
from concordia.typing import prefab as prefab_lib  # type: ignore[import-untyped]

from story_engine.concordia_runtime.components import (
    CharacterContext,
    LocalePolicy,
)
from story_engine.domain.recipe import AgentRecipe


@dataclasses.dataclass
class StoryCharacterPrefab(prefab_lib.Prefab):  # type: ignore[misc]
    """Persistent story character assembled from a validated AgentRecipe."""

    description: str = "A story character with private memory and locale policy."
    params: Mapping[str, str] = dataclasses.field(default_factory=dict)
    recipe: AgentRecipe | None = None

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        if self.recipe is None:
            raise ValueError("StoryCharacterPrefab requires an AgentRecipe")
        name = self.params.get("name")
        if not name:
            raise ValueError("story character requires a name")

        memory_key = agent_components.memory.DEFAULT_MEMORY_COMPONENT_KEY
        components: dict[str, object] = {
            "system_instruction": agent_components.constant.Constant(
                state=self.recipe.system_instruction_text,
                pre_act_label="Story role",
            ),
            "locale": LocalePolicy(self.recipe.content_locale),
            "actor_state": agent_components.constant.Constant(
                state=self.params.get("actor_state", ""),
                pre_act_label="Current Actor State",
            ),
            "observation_to_memory": (
                agent_components.observation.ObservationToMemory()
            ),
            "knowledge": CharacterContext(
                project_root=self.params["project_root"],
                branch_id=self.params["branch_id"],
                subject_id=name,
            ),
            memory_key: agent_components.memory.AssociativeMemory(
                memory_bank=memory_bank
            ),
        }
        component_order = (
            "system_instruction",
            "locale",
            "actor_state",
            "knowledge",
            "observation_to_memory",
            memory_key,
        )
        act_component = agent_components.concat_act_component.ConcatActComponent(
            model=model,
            component_order=component_order,
            prefix_entity_name=False,
            randomize_choices=False,
        )
        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name,
            act_component=act_component,
            context_components=components,
        )
