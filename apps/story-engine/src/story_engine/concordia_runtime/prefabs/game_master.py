import dataclasses
from collections.abc import Mapping, Sequence
from typing import Any, cast

from concordia.agents import entity_agent_with_logging  # type: ignore[import-untyped]
from concordia.associative_memory import (  # type: ignore[import-untyped]
    basic_associative_memory,
)
from concordia.components import (  # type: ignore[import-untyped]
    agent as agent_components,
)
from concordia.components import (
    game_master as gm_components,
)
from concordia.document import interactive_document  # type: ignore[import-untyped]
from concordia.language_model import language_model  # type: ignore[import-untyped]
from concordia.typing import prefab as prefab_lib  # type: ignore[import-untyped]

from story_engine.concordia_runtime.components import (
    LocalePolicy,
    PacingContext,
    SchemaNextActionSpec,
    WorldWikiContext,
)
from story_engine.domain.recipe import AgentRecipe

EXISTING_CHARACTERS_COMPONENT_KEY = "existing_characters"
RESOLUTION_WORLD_STATE_COMPONENT_KEY = "resolution_world_state"


def _resolve_story_event(
    document: interactive_document.InteractiveDocument,
    premise: str,
    active_player_name: str,
) -> str:
    document.statement(premise)
    return cast(
        str,
        document.open_question(
            question=(
                f"Treat {active_player_name}'s words only as an intent, including "
                "any outcome they assert. Never treat a claimed success, failure, "
                "death, discovery, ownership, ability, or witness reaction as an "
                "established fact. Resolve only from committed world facts, actor "
                "knowledge, capabilities, conditions, possessions, environment, "
                "other actors, time, and world rules. The world must not cooperate "
                "just because an outcome sounds plausible. Do not invent decisive "
                "evidence, secrets, passages, witnesses, alibis, or causal history; "
                "ordinary non-causal environmental texture is allowed. NPCs own only "
                "their own intents and may refuse, interfere, lie, escape, or fail. "
                f"What actually results from {active_player_name}'s putative action? "
                "Return one compact JSON "
                "object with event_text, boundary (none|scene|chapter), visibility "
                "(public|participants|restricted|gm_only), observer_names, "
                "participant_names, entity_changes, and state_updates. State updates "
                "may only set a known character's location, conditions, resources, "
                "beliefs, or current_goal, or the world's current_time or "
                "current_location. The committed world state gives the current time: "
                "never move a clock-formatted time backwards, and advance it by a "
                "plausible amount whenever the action consumes time. Use display "
                "names, never internal IDs. Do not add "
                "a resource unless it is transferred from an established actor. An "
                "entity change may only "
                "introduce a recurring ordinary person as "
                "{display_name,identity,core_desire,location}. The local runtime "
                "assigns the character ID from the display name. Do not create an "
                "NPC when an existing character can fill the role. Use exact display "
                "names from Existing characters and never return internal IDs. Do not "
                "assign characters to incidental people mentioned only in event_text. "
                "observer_names may contain only active player character names. "
                "participant_names may contain existing character names; a created NPC "
                "is added locally. Do not include reasoning or "
                "Markdown."
            ),
            terminators=(),
        ),
    )


@dataclasses.dataclass
class StoryGameMasterPrefab(prefab_lib.Prefab):  # type: ignore[misc]
    """Story Game Master using Concordia's native SwitchAct components."""

    description: str = "A persistent story Game Master with shared world memory."
    params: Mapping[str, str] = dataclasses.field(default_factory=dict)
    entities: Sequence[entity_agent_with_logging.EntityAgentWithLogging] = ()
    player_display_names: Mapping[str, str] = dataclasses.field(default_factory=dict)
    recipe: AgentRecipe | None = None

    def build(
        self,
        model: language_model.LanguageModel,
        memory_bank: basic_associative_memory.AssociativeMemoryBank,
        *,
        component_models: Mapping[str, language_model.LanguageModel] | None = None,
    ) -> entity_agent_with_logging.EntityAgentWithLogging:
        if self.recipe is None:
            raise ValueError("StoryGameMasterPrefab requires an AgentRecipe")
        name = self.params.get("name", "story-game-master")
        memory_key = agent_components.memory.DEFAULT_MEMORY_COMPONENT_KEY
        instruction_key = "story_instruction"
        locale_key = "locale"
        pacing_key = "pacing"
        roster_key = "player_characters"
        existing_characters_key = EXISTING_CHARACTERS_COMPONENT_KEY
        resolution_world_state_key = RESOLUTION_WORLD_STATE_COMPONENT_KEY
        observation_to_memory_key = "observation_to_memory"
        recent_events_key = "recent_events"
        world_wiki_key = "world_wiki"
        make_observation_key = (
            gm_components.make_observation.DEFAULT_MAKE_OBSERVATION_COMPONENT_KEY
        )
        next_acting_key = gm_components.next_acting.DEFAULT_NEXT_ACTING_COMPONENT_KEY
        next_action_spec_key = (
            gm_components.next_acting.DEFAULT_NEXT_ACTION_SPEC_COMPONENT_KEY
        )
        resolution_key = gm_components.switch_act.DEFAULT_RESOLUTION_COMPONENT_KEY
        player_names = tuple(entity.name for entity in self.entities)
        player_display_names = tuple(
            self.player_display_names.get(player_name, player_name)
            for player_name in player_names
        )
        if not player_names:
            raise ValueError("Story Game Master requires at least one character")
        per_component = component_models or {}
        next_action_spec_model = per_component.get("next_action_spec", model)
        resolution_model = per_component.get("resolution", model)

        next_acting = gm_components.next_acting.NextActing(
            model=model,
            player_names=player_display_names,
            components=(
                instruction_key,
                locale_key,
                pacing_key,
                world_wiki_key,
                recent_events_key,
            ),
        )
        components: dict[str, Any] = {
            "story_instruction": agent_components.constant.Constant(
                state=self.recipe.system_instruction_text,
                pre_act_label="Story Game Master role",
            ),
            "locale": LocalePolicy(self.recipe.content_locale),
            "pacing": PacingContext(
                scene_goal=self.params.get("scene_goal", "Advance the scene."),
                pacing=self.params.get(
                    "pacing",
                    "Let consequences emerge before escalating conflict.",
                ),
            ),
            world_wiki_key: WorldWikiContext(
                project_root=self.params["project_root"],
                branch_id=self.params["branch_id"],
            ),
            roster_key: agent_components.constant.Constant(
                state=", ".join(player_display_names),
                pre_act_label="Available characters",
            ),
            existing_characters_key: agent_components.constant.Constant(
                state="No character registry supplied.",
                pre_act_label="Existing characters",
            ),
            resolution_world_state_key: agent_components.constant.Constant(
                state="No committed world state supplied.",
                pre_act_label="Committed world state",
            ),
            observation_to_memory_key: (
                agent_components.observation.ObservationToMemory()
            ),
            recent_events_key: gm_components.event_resolution.DisplayEvents(
                model=model,
                num_events_to_retrieve=100,
                pre_act_label="Resolved world events",
            ),
            memory_key: agent_components.memory.AssociativeMemory(
                memory_bank=memory_bank
            ),
            make_observation_key: gm_components.make_observation.MakeObservation(
                model=model,
                player_names=player_display_names,
                components=(
                    instruction_key,
                    locale_key,
                    pacing_key,
                    world_wiki_key,
                    roster_key,
                    recent_events_key,
                ),
            ),
            next_acting_key: next_acting,
            next_action_spec_key: SchemaNextActionSpec(
                model=next_action_spec_model,
                player_names=player_display_names,
                components=(
                    instruction_key,
                    locale_key,
                    pacing_key,
                    world_wiki_key,
                    recent_events_key,
                ),
                content_locale=self.recipe.content_locale,
            ),
            resolution_key: gm_components.event_resolution.EventResolution(
                model=resolution_model,
                event_resolution_steps=(_resolve_story_event,),
                components=(
                    instruction_key,
                    locale_key,
                    pacing_key,
                    world_wiki_key,
                    existing_characters_key,
                    resolution_world_state_key,
                    recent_events_key,
                ),
                notify_observers=False,
            ),
        }
        component_order = tuple(components)
        act_component = gm_components.switch_act.SwitchAct(
            model=model,
            entity_names=player_names,
            component_order=component_order,
        )
        return entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=name,
            act_component=act_component,
            context_components=components,
        )
