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
from concordia.typing import entity as entity_lib  # type: ignore[import-untyped]
from concordia.typing import prefab as prefab_lib

from story_engine.concordia_runtime.components import (
    EligibleNextActing,
    LocalePolicy,
    PacingContext,
    SchemaNextActionSpec,
    WorldWikiContext,
)
from story_engine.domain.recipe import AgentRecipe

EXISTING_CHARACTERS_COMPONENT_KEY = "existing_characters"
PERCEPTION_CONTEXT_COMPONENT_KEY = "perception_context"
RESOLUTION_WORLD_STATE_COMPONENT_KEY = "resolution_world_state"
PERCEPTION_CALL_TEMPLATE = (
    "Report only what {name} can currently perceive. "
    "Do not invent or emphasize details for plot progression."
)


class BoundedMakeObservation(gm_components.make_observation.MakeObservation):  # type: ignore[misc]
    """Generate one actor observation without plot-progression authority."""

    def pre_act(self, action_spec: entity_lib.ActionSpec) -> str:
        if action_spec.output_type != entity_lib.OutputType.MAKE_OBSERVATION:
            return ""
        prompt = interactive_document.InteractiveDocument(self._model)
        component_states = "\n".join(
            self._component_pre_act_display(key) for key in self._components
        )
        prompt.statement(f"{component_states}\n")
        active_entity_name = self._get_active_entity_name_from_call_to_action(
            action_spec.call_to_action
        )
        events = self._queue.get_and_clear(active_entity_name)
        if events:
            result = "\n\n\n".join(events) + "\n\n\n"
        else:
            result = cast(
                str,
                prompt.open_question(
                    question=(
                        f"What can {active_entity_name} perceive right now? "
                        "Return only currently perceivable information."
                    ),
                    terminators=(),
                ),
            )
        self._logging_channel(
            {
                "Key": self._pre_act_label,
                "Summary": result,
                "Value": result,
                "Prompt": prompt.view().text(),
            }
        )
        return result


def _resolve_story_event(
    document: interactive_document.InteractiveDocument,
    premise: str,
    active_player_name: str,
) -> str:
    del premise
    view = getattr(document, "view", None)
    context_text = ""
    if callable(view):
        rendered = view()
        context_text = str(getattr(rendered, "text", lambda: "")())
    if "World Initiative Mode:" in context_text:
        question = (
            "Produce one concrete external world change caused by the stated "
            "Clock, Pressure, or stagnation trigger. Use only committed World "
            "state and recent causal events. Do not decide any Actor's beliefs, "
            "goal, intent, voluntary dialogue, or voluntary action. Commit only "
            "what objectively happens now; do not optimize for drama or pacing."
        )
    else:
        question = (
            "1. Actor input is intent, never committed fact.\n"
            "2. Resolve from committed world state, ability, condition, "
            "resources and opportunity.\n"
            "3. Never invent hidden facts, decisive evidence or resources.\n"
            "4. Never decide voluntary behavior for another Actor.\n"
            "5. Resolve only the first meaningful uncertainty of compound "
            "actions.\n"
            "6. Commit only what actually happened and its causal consequences.\n\n"
            f"Resolve only {active_player_name}'s own first attempt. Stop when "
            "another Actor's voluntary response would be required."
        )
    return cast(
        str,
        document.open_question(
            question=question,
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
        perception_context_key = PERCEPTION_CONTEXT_COMPONENT_KEY
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

        next_acting = EligibleNextActing(
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
            perception_context_key: agent_components.constant.Constant(
                state="No actor-specific perception context supplied.",
                pre_act_label="Current actor perception context",
            ),
            resolution_world_state_key: agent_components.constant.Constant(
                state="No authoritative resolution context supplied.",
                pre_act_label="Authoritative resolution context",
            ),
            observation_to_memory_key: (
                agent_components.observation.ObservationToMemory()
            ),
            recent_events_key: gm_components.event_resolution.DisplayEvents(
                model=model,
                num_events_to_retrieve=4,
                pre_act_label="Resolved world events",
            ),
            memory_key: agent_components.memory.AssociativeMemory(
                memory_bank=memory_bank
            ),
            make_observation_key: BoundedMakeObservation(
                model=model,
                player_names=player_display_names,
                components=(
                    locale_key,
                    perception_context_key,
                ),
                call_to_make_observation=PERCEPTION_CALL_TEMPLATE,
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
                    locale_key,
                    existing_characters_key,
                    resolution_world_state_key,
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
