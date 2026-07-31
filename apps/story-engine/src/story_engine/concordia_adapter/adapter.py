import json
from threading import Event

from concordia.agents import entity_agent_with_logging  # type: ignore[import-untyped]
from concordia.components import (  # type: ignore[import-untyped]
    agent as agent_components,
)
from concordia.components.game_master import switch_act  # type: ignore[import-untyped]
from concordia.typing import entity as entity_lib  # type: ignore[import-untyped]
from pydantic import BaseModel

from story_engine.concordia_adapter.language_model import JanGatewayLanguageModel
from story_engine.domain.models import (
    Character,
    CharacterIntent,
    WorldOutcome,
    WorldState,
)
from story_engine.evolution.context import CharacterContext
from story_engine.models.gateway import ModelGateway


def _schema(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), ensure_ascii=False)


def _json(value: BaseModel | tuple[BaseModel, ...]) -> str:
    payload: object
    if isinstance(value, tuple):
        payload = [item.model_dump(mode="json") for item in value]
    else:
        payload = value.model_dump(mode="json")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class ConcordiaStoryAdapter:
    """Convert Story Engine contracts to Concordia entities and back."""

    def __init__(
        self,
        gateway: ModelGateway,
        *,
        cancellation: Event | None = None,
    ) -> None:
        self._gateway = gateway
        self._cancellation = cancellation

    def generate_intent(self, context: CharacterContext) -> CharacterIntent:
        model = JanGatewayLanguageModel(
            self._gateway,
            profile_id="character",
            task_type="character",
            output_schema=_schema(CharacterIntent),
            cancellation=self._cancellation,
        )
        components = {
            "instructions": agent_components.constant.Constant(
                state=(
                    "You are one story character. Decide only an intended action. "
                    "Do not decide success, another character's response, or new "
                    "world facts. Use only the supplied visible fact IDs."
                ),
                pre_act_label="Story Engine role",
            ),
            "private_context": agent_components.constant.Constant(
                state=_json(context),
                pre_act_label="Authorized private character context",
            ),
        }
        actor = entity_agent_with_logging.EntityAgentWithLogging(
            agent_name=context.character.id,
            act_component=agent_components.concat_act_component.ConcatActComponent(
                model=model,
                component_order=tuple(components),
                prefix_entity_name=False,
                randomize_choices=False,
            ),
            context_components=components,
        )
        raw = actor.act(
            entity_lib.free_action_spec(
                call_to_action=(
                    "Return exactly one CharacterIntent JSON object for {name}."
                ),
                tag="story-character-intent",
            )
        )
        intent = CharacterIntent.model_validate_json(raw)
        if intent.character_id != context.character.id:
            raise ValueError("Concordia returned an intent for another character")
        if not set(intent.knowledge_basis).issubset(context.visible_fact_ids):
            raise ValueError("Concordia intent used facts outside its private context")
        return intent

    def resolve(
        self,
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        characters: tuple[Character, ...],
    ) -> WorldOutcome:
        return self._resolve(world, intents, characters)

    def revise(
        self,
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        characters: tuple[Character, ...],
        previous_outcome: WorldOutcome,
        instruction: str,
    ) -> WorldOutcome:
        if not instruction.strip():
            raise ValueError("Concordia revision requires an instruction")
        return self._resolve(
            world,
            intents,
            characters,
            previous_outcome=previous_outcome,
            revision_instruction=instruction,
        )

    def _resolve(
        self,
        world: WorldState,
        intents: tuple[CharacterIntent, ...],
        characters: tuple[Character, ...],
        *,
        previous_outcome: WorldOutcome | None = None,
        revision_instruction: str | None = None,
    ) -> WorldOutcome:
        if not intents:
            raise ValueError("Concordia resolver requires at least one intent")
        model = JanGatewayLanguageModel(
            self._gateway,
            profile_id="resolver",
            task_type="resolver",
            output_schema=_schema(WorldOutcome),
            cancellation=self._cancellation,
        )
        instructions = (
            "You are the World Resolver. Resolve all supplied intentions "
            "together against the current world. Characters decide intent; "
            "only you decide outcomes. Reuse an existing character whenever "
            "that character can reasonably fulfill a required role. Create a "
            "minimal new NPC only when no existing character can fulfill it. "
            "A new NPC is not an active agent. Return candidate state changes "
            "only; never commit canonical state."
        )
        if revision_instruction is not None:
            instructions += (
                " Replace the previous non-canonical outcome according to the "
                "requested revision while preserving the supplied character "
                "intents and canonical source values. Produce a genuinely revised "
                "WorldOutcome; do not merely append the instruction to its summary."
            )
        components = {
            "instructions": agent_components.constant.Constant(
                state=instructions,
                pre_act_label="Story Engine role",
            ),
            "world": agent_components.constant.Constant(
                state=_json(world),
                pre_act_label="Canonical world snapshot",
            ),
            "intents": agent_components.constant.Constant(
                state=_json(intents),
                pre_act_label="Current isolated character intents",
            ),
            "existing_characters": agent_components.constant.Constant(
                state=json.dumps(
                    [
                        {
                            "id": character.id,
                            "display_name": character.display_name,
                            "type": character.type,
                            "identity": character.identity,
                            "current_goal": character.current_goal,
                            "location": character.location,
                        }
                        for character in characters
                    ],
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                pre_act_label="Existing character roster for reuse",
            ),
        }
        if previous_outcome is not None and revision_instruction is not None:
            components["previous_outcome"] = agent_components.constant.Constant(
                state=_json(previous_outcome),
                pre_act_label="Previous non-canonical outcome",
            )
            components["revision_instruction"] = agent_components.constant.Constant(
                state=revision_instruction,
                pre_act_label="Requested revision",
            )
        resolver = entity_agent_with_logging.EntityAgentWithLogging(
            agent_name="world-resolver",
            act_component=switch_act.SwitchAct(
                model=model,
                entity_names=tuple(intent.character_id for intent in intents),
                component_order=tuple(components),
            ),
            context_components=components,
        )
        raw = resolver.act(
            entity_lib.ActionSpec(
                call_to_action="Return exactly one WorldOutcome JSON object.",
                output_type=entity_lib.OutputType.RESOLVE,
                tag=(
                    "story-world-outcome-revision"
                    if revision_instruction is not None
                    else "story-world-outcome"
                ),
            )
        )
        return WorldOutcome.model_validate_json(raw)
