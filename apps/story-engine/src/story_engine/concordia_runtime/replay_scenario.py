from story_engine.concordia_runtime.factory import (
    ConcordiaActorFactory,
    default_character_recipe,
    default_game_master_recipe,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.replay import ReplayLanguageModel
from story_engine.domain.memory import MemoryScope
from story_engine.domain.simulation import TurnSessionRequest
from story_engine.simulation.engine import RuntimeFactory
from story_engine.simulation.runtime import StorySimulationRuntime


def replay_runtime_factory() -> RuntimeFactory:
    """Build deterministic runtimes for soak tests and local benchmarks."""

    def build(
        session_id: str,
        request: TurnSessionRequest,
    ) -> StorySimulationRuntime:
        steps = request.control.max_steps
        actor_model = ReplayLanguageModel(
            text_responses=tuple(f"Action {step}" for step in range(steps))
        )
        gm_model = ReplayLanguageModel(
            text_responses=tuple(
                value
                for step in range(steps)
                for value in (
                    f"Observation {step}",
                    '{"call_to_action":"Act now.","output_type":"free",'
                    '"options":[],"tag":"action"}',
                    f"Resolved event {step}",
                )
            ),
            choice_responses=tuple(
                value for _ in range(steps) for value in ("No", "actor-a", "none")
            ),
        )
        factory = ConcordiaActorFactory({"actor": actor_model, "gm": gm_model})
        actor = factory.build_actor(
            default_character_recipe(
                model_profile_id="actor",
                content_locale=request.content_locale,
            ),
            actor_params={"name": "actor-a", "identity": "Investigator"},
            memory=ConcordiaMemoryBank(
                owner_id="actor-a",
                scope=MemoryScope.CHARACTER,
            ),
        )
        game_master = factory.build_game_master(
            default_game_master_recipe(
                model_profile_id="gm",
                content_locale=request.content_locale,
            ),
            gm_params={"name": "gm", "scene_goal": request.premise_text},
            actors=(actor,),
            shared_memory=ConcordiaMemoryBank(
                owner_id="gm",
                scope=MemoryScope.GAME_MASTER,
            ),
        )
        return StorySimulationRuntime(
            project_id=request.project_id,
            session_id=session_id,
            branch_id=request.branch_id,
            content_locale=request.content_locale,
            actors=(actor,),
            game_master=game_master,
        )

    return build
