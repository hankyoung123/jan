import copy
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from concordia.agents import entity_agent_with_logging  # type: ignore[import-untyped]
from concordia.components import (  # type: ignore[import-untyped]
    game_master as gm_components,
)
from concordia.language_model import language_model  # type: ignore[import-untyped]
from pydantic import JsonValue

from story_engine.concordia_runtime.action_spec import ConcordiaActionSpecCodec
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.prefabs import (
    StoryCharacterPrefab,
    StoryGameMasterPrefab,
)
from story_engine.concordia_runtime.prefabs.game_master import (
    EXISTING_CHARACTERS_COMPONENT_KEY,
    RESOLUTION_WORLD_STATE_COMPONENT_KEY,
)
from story_engine.domain.action import (
    ActionOutputType,
    ActionSpec,
    EntityRole,
)
from story_engine.domain.memory import MemoryRecord, MemoryRecordType
from story_engine.domain.recipe import AgentRecipe, PerceptionFrame
from story_engine.domain.simulation import ActorPhase, StoryActor


class ConcordiaStoryActor:
    """Project-side view of one persistent Concordia entity."""

    def __init__(
        self,
        entity: entity_agent_with_logging.EntityAgentWithLogging,
        *,
        role: EntityRole,
        memory: ConcordiaMemoryBank,
        display_name: str | None = None,
    ) -> None:
        self._entity = entity
        self._role = role
        self._memory = memory
        self._display_name = display_name or entity.name

    @property
    def name(self) -> str:
        return cast(str, self._entity.name)

    @property
    def display_name(self) -> str:
        return self._display_name

    @property
    def role(self) -> EntityRole:
        return self._role

    @property
    def entity(self) -> entity_agent_with_logging.EntityAgentWithLogging:
        return self._entity

    @property
    def memory(self) -> ConcordiaMemoryBank:
        return self._memory

    def act(self, action_spec: ActionSpec) -> str:
        return cast(
            str,
            self._entity.act(ConcordiaActionSpecCodec.to_concordia(action_spec)),
        )

    def observe(self, perception: PerceptionFrame | str) -> None:
        if isinstance(perception, str):
            observation = perception
        else:
            record = MemoryRecord(
                record_id=perception.frame_id,
                record_type=MemoryRecordType.OBSERVATION,
                scope=self._memory.scope,
                owner_id=self.name,
                session_id=perception.session_id,
                branch_id=perception.branch_id,
                step=perception.step,
                text=perception.observation_text,
                content_locale=perception.content_locale,
                created_at=datetime.now().astimezone(),
                actor_ids=tuple(
                    dict.fromkeys(
                        (
                            perception.actor_id,
                            *perception.participant_ids,
                            *(
                                entity.entity_id
                                for entity in perception.visible_entities
                            ),
                        )
                    )
                ),
                location_ids=perception.location_ids,
                source_record_ids=perception.source_record_ids,
                visible_to=(perception.actor_id,),
                tags=("observation",),
            )
            observation = self._memory.codec.encode(record)
        self._entity.observe(observation)

    def get_phase(self) -> ActorPhase:
        return ActorPhase(self._entity.get_phase().name.lower())

    def get_state(self) -> dict[str, JsonValue]:
        return cast(dict[str, JsonValue], copy.deepcopy(self._entity.get_state()))

    def set_state(self, state: Mapping[str, JsonValue]) -> None:
        self._entity.set_state(cast(dict[str, Any], state))

    def set_content_locale(self, content_locale: str) -> None:
        state = self.get_state()
        components = state.get("context_components")
        if not isinstance(components, dict):
            raise ValueError("actor state has no context components")
        locale = components.get("locale")
        if not isinstance(locale, dict):
            raise ValueError("actor state has no locale component")
        locale["content_locale"] = content_locale
        self.set_state(state)

    def set_actor_state(self, actor_state_text: str) -> None:
        """Refresh the derived Character context without owning business state."""
        state = self.get_state()
        components = state.get("context_components")
        if not isinstance(components, dict):
            raise ValueError("actor state has no context components")
        actor_state = components.get("actor_state")
        if not isinstance(actor_state, dict):
            raise ValueError("actor state has no ActorStateContext component")
        actor_state["state"] = actor_state_text
        self.set_state(state)

    def get_last_log(self) -> Mapping[str, JsonValue]:
        return cast(dict[str, JsonValue], self._entity.get_last_log())


class ConcordiaGameMasterActor(ConcordiaStoryActor):
    def set_active_actor(self, actor_id: str) -> None:
        """Set the Concordia turn owner before resolving a direct human intent."""
        state = self._entity.get_state()
        components = state.get("context_components")
        if not isinstance(components, dict):
            raise ValueError("Game Master state has no context components")
        next_acting = components.get(
            gm_components.next_acting.DEFAULT_NEXT_ACTING_COMPONENT_KEY
        )
        if not isinstance(next_acting, dict):
            raise ValueError("Game Master state has no next-acting component")
        next_acting["currently_active_player"] = actor_id
        self._entity.set_state(state)

    def set_resolution_character_registry(self, registry_text: str) -> None:
        state = self.get_state()
        components = state.get("context_components")
        if not isinstance(components, dict):
            raise ValueError("Game Master state has no context components")
        registry = components.get(EXISTING_CHARACTERS_COMPONENT_KEY)
        if not isinstance(registry, dict):
            raise ValueError("Game Master state has no character registry component")
        registry["state"] = registry_text
        self.set_state(state)

    def set_resolution_world_state(self, world_state_text: str) -> None:
        state = self.get_state()
        components = state.get("context_components")
        if not isinstance(components, dict):
            raise ValueError("Game Master state has no context components")
        world_state = components.get(RESOLUTION_WORLD_STATE_COMPONENT_KEY)
        if not isinstance(world_state, dict):
            raise ValueError("Game Master state has no world state component")
        world_state["state"] = world_state_text
        self.set_state(state)

    def set_resolution_context(self, context_text: str) -> None:
        """Refresh the explicit resolver context on the stable state key."""
        self.set_resolution_world_state(context_text)

    def make_observation(
        self,
        actor: StoryActor,
        *,
        session_id: str,
        step: int,
        content_locale: str,
    ) -> PerceptionFrame:
        observation = self.act(
            ActionSpec(
                spec_id=f"observation:{session_id}:{step}:{actor.name}",
                output_type=ActionOutputType.MAKE_OBSERVATION,
                call_to_action=(
                    f"What is the current situation faced by {actor.display_name}? "
                    "What do they now observe? Only include information of which "
                    "they are aware."
                ),
                content_locale=content_locale,
                tag="observation",
            )
        )
        return PerceptionFrame(
            frame_id=f"observation:{session_id}:{step}:{actor.name}",
            session_id=session_id,
            branch_id="runtime",
            actor_id=actor.name,
            step=step,
            content_locale=content_locale,
            observation_text=observation,
            generated_by_gm=True,
        )

    def select_next_actor(
        self,
        actors: Sequence[StoryActor],
        *,
        session_id: str,
        step: int,
    ) -> str:
        actors_by_display_name = {
            actor.display_name.strip().casefold(): actor for actor in actors
        }
        if len(actors_by_display_name) != len(actors):
            raise ValueError("active actor display names must be unique")
        actor_names = tuple(actor.display_name for actor in actors)
        selected = self.act(
            ActionSpec(
                spec_id=f"next-actor:{session_id}:{step}",
                output_type=ActionOutputType.NEXT_ACTING,
                call_to_action="Who is next to act?",
                options=actor_names,
                content_locale="en-US",
                tag="next_acting",
            )
        )
        actor = actors_by_display_name.get(selected.strip().casefold())
        if actor is None:
            raise ValueError(f"Game Master selected unknown actor name {selected!r}")
        return actor.name

    def create_action_spec(
        self,
        actor: StoryActor,
        *,
        session_id: str,
        step: int,
        content_locale: str,
    ) -> ActionSpec:
        raw = self.act(
            ActionSpec(
                spec_id=f"next-action-spec:{session_id}:{step}",
                output_type=ActionOutputType.NEXT_ACTION_SPEC,
                call_to_action=(
                    f"In what action spec format should {actor.display_name} respond?"
                ),
                content_locale=content_locale,
                tag="next_action_spec",
            )
        )
        return ConcordiaActionSpecCodec.from_json(
            raw,
            spec_id=f"actor-action:{session_id}:{step}",
            content_locale=content_locale,
        )

    def should_terminate(
        self,
        *,
        session_id: str,
        step: int,
    ) -> tuple[bool, str | None]:
        result = self.act(
            ActionSpec(
                spec_id=f"terminate:{session_id}:{step}",
                output_type=ActionOutputType.TERMINATE,
                call_to_action="Should this story session terminate now?",
                options=("Yes", "No"),
                option_ids=("yes", "no"),
                content_locale="en-US",
                tag="terminate",
            )
        )
        normalized = result.strip().casefold()
        if normalized not in {"yes", "no"}:
            raise ValueError("Game Master returned an invalid termination decision")
        return normalized == "yes", None


class ConcordiaActorFactory:
    """Build and restore persistent actor and Game Master instances."""

    def __init__(
        self,
        models: Mapping[str, language_model.LanguageModel],
    ) -> None:
        self._models = dict(models)
        self._names: set[str] = set()

    def _model_for(self, recipe: AgentRecipe) -> language_model.LanguageModel:
        try:
            return self._models[recipe.model_profile_id]
        except KeyError as error:
            raise ValueError(
                f"model profile {recipe.model_profile_id!r} is unavailable"
            ) from error

    def register_model(
        self,
        profile_id: str,
        model: language_model.LanguageModel,
    ) -> None:
        if profile_id in self._models:
            raise ValueError(f"model profile {profile_id!r} already exists")
        self._models[profile_id] = model

    def _claim_name(self, name: str) -> None:
        if name in self._names:
            raise ValueError(f"entity ID {name!r} already exists")
        self._names.add(name)

    def build_actor(
        self,
        recipe: AgentRecipe,
        *,
        actor_params: Mapping[str, str],
        memory: ConcordiaMemoryBank,
        initial_state: Mapping[str, JsonValue] | None = None,
    ) -> ConcordiaStoryActor:
        if recipe.role != EntityRole.CHARACTER:
            raise ValueError("character factory requires a character recipe")
        name = actor_params.get("name")
        if not name:
            raise ValueError("actor_params must include a name")
        if memory.owner_id != name:
            raise ValueError("character memory owner must match actor name")
        self._claim_name(name)
        try:
            entity = StoryCharacterPrefab(
                params=dict(actor_params),
                recipe=recipe,
            ).build(self._model_for(recipe), memory.raw_bank)
            if initial_state is not None:
                entity.set_state(cast(dict[str, Any], initial_state))
        except Exception:
            self._names.remove(name)
            raise
        return ConcordiaStoryActor(
            entity,
            role=EntityRole.CHARACTER,
            memory=memory,
            display_name=actor_params.get("display_name"),
        )

    def build_game_master(
        self,
        recipe: AgentRecipe,
        *,
        gm_params: Mapping[str, str],
        actors: Sequence[ConcordiaStoryActor],
        shared_memory: ConcordiaMemoryBank,
        initial_state: Mapping[str, JsonValue] | None = None,
        component_models: Mapping[str, language_model.LanguageModel] | None = None,
    ) -> ConcordiaGameMasterActor:
        if recipe.role != EntityRole.GAME_MASTER:
            raise ValueError("Game Master factory requires a game_master recipe")
        name = gm_params.get("name", "story-game-master")
        if shared_memory.owner_id != name:
            raise ValueError("shared memory owner must match Game Master name")
        self._claim_name(name)
        raw_actors = tuple(actor.entity for actor in actors)
        try:
            entity = StoryGameMasterPrefab(
                params=dict(gm_params),
                entities=raw_actors,
                player_display_names={
                    actor.name: actor.display_name for actor in actors
                },
                recipe=recipe,
            ).build(
                self._model_for(recipe),
                shared_memory.raw_bank,
                component_models=component_models,
            )
            if initial_state is not None:
                entity.set_state(cast(dict[str, Any], initial_state))
        except Exception:
            self._names.remove(name)
            raise
        return ConcordiaGameMasterActor(
            entity,
            role=EntityRole.GAME_MASTER,
            memory=shared_memory,
            display_name=name,
        )


def default_character_recipe(
    *,
    model_profile_id: str = "actor",
    content_locale: str = "zh-CN",
) -> AgentRecipe:
    from story_engine.domain.recipe import ComponentRecipe

    return AgentRecipe(
        recipe_id="character.default",
        version="v1",
        role=EntityRole.CHARACTER,
        prefab_type="story_character",
        model_profile_id=model_profile_id,
        components=(
            ComponentRecipe(
                component_id="actor_state",
                component_type="actor_state",
                order=0,
            ),
            ComponentRecipe(
                component_id="knowledge", component_type="knowledge", order=1
            ),
        ),
        content_locale=content_locale,
        system_instruction_text=(
            "Act only from the current Actor State and private observations. "
            "Propose intent; never decide world outcomes."
        ),
    )


def default_game_master_recipe(
    *,
    model_profile_id: str = "game-master",
    content_locale: str = "zh-CN",
) -> AgentRecipe:
    from story_engine.domain.recipe import ComponentRecipe

    return AgentRecipe(
        recipe_id="game-master.default",
        version="v1",
        role=EntityRole.GAME_MASTER,
        prefab_type="story_game_master",
        model_profile_id=model_profile_id,
        components=(
            ComponentRecipe(
                component_id="world_memory",
                component_type="shared_memory",
                order=0,
            ),
            ComponentRecipe(component_id="pacing", component_type="pacing", order=1),
        ),
        content_locale=content_locale,
        system_instruction_text=(
            "Maintain shared world truth. Choose the next actor and action form, "
            "resolve putative actions into events, route observations, and decide "
            "when the scene ends."
        ),
    )
