import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from story_engine.concordia_runtime.factory import (
    ConcordiaActorFactory,
    default_character_recipe,
    default_game_master_recipe,
)
from story_engine.concordia_runtime.language_model import JanConcordiaLanguageModel
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.simulation import TurnSessionRequest
from story_engine.domain.trace import ModelCallTrace
from story_engine.models.gateway import ModelGateway
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.simulation.runtime import StorySimulationRuntime
from story_engine.workspace.project_store import ProjectStore


class ProjectRuntimeFactory:
    """Build a branch-local runtime from project seeds and model profiles."""

    def __init__(
        self,
        projects_root: Path,
        gateway: ModelGateway,
        *,
        trace_sink: Callable[[ModelCallTrace], None] | None = None,
    ) -> None:
        self._projects_root = projects_root
        self._gateway = gateway
        self._trace_sink = trace_sink

    def __call__(
        self,
        session_id: str,
        request: TurnSessionRequest,
    ) -> StorySimulationRuntime:
        snapshot = ProjectStore(self._projects_root / request.project_id).load()
        active = tuple(
            character for character in snapshot.characters if character.type == "active"
        )
        if request.actor_ids:
            by_id = {character.id: character for character in active}
            unknown = set(request.actor_ids) - set(by_id)
            if unknown:
                raise ValueError(f"unknown or inactive actor IDs: {sorted(unknown)}")
            active = tuple(by_id[actor_id] for actor_id in request.actor_ids)
        if not active:
            raise ValueError("simulation requires at least one active character")

        cancellation = Event()
        model_traces: list[ModelCallTrace] = []

        def record_trace(trace: ModelCallTrace) -> None:
            model_traces.append(trace)
            if self._trace_sink is not None:
                self._trace_sink(trace)

        models = {}
        for character in active:
            key = f"actor:{character.id}"
            models[key] = JanConcordiaLanguageModel(
                self._gateway,
                profile_id="actor",
                task_type="actor",
                content_locale=request.content_locale,
                session_id=session_id,
                branch_id=request.branch_id,
                actor_id=character.id,
                cancellation=cancellation,
                trace_sink=record_trace,
            )
        gm_model_key = "game-master"
        models[gm_model_key] = JanConcordiaLanguageModel(
            self._gateway,
            profile_id="game-master",
            task_type="game_master",
            content_locale=request.content_locale,
            session_id=session_id,
            branch_id=request.branch_id,
            cancellation=cancellation,
            trace_sink=record_trace,
        )
        factory = ConcordiaActorFactory(models)
        actors = []
        facts_by_id = {fact.id: fact for fact in snapshot.facts}
        public_fact_ids = set(snapshot.world.public_fact_ids)
        for character in active:
            memory = ConcordiaMemoryBank(
                owner_id=character.id,
                scope=MemoryScope.CHARACTER,
            )
            visible_fact_ids = public_fact_ids | set(character.known_fact_ids)
            for fact_id in sorted(visible_fact_ids):
                fact = facts_by_id[fact_id]
                memory.add(
                    MemoryRecord(
                        record_id=f"seed:{fact.id}:{character.id}",
                        record_type=MemoryRecordType.PREMISE,
                        scope=MemoryScope.CHARACTER,
                        owner_id=character.id,
                        session_id=session_id,
                        branch_id=request.branch_id,
                        step=0,
                        text=fact.statement,
                        content_locale=request.content_locale,
                        created_at=fact.introduced_at,
                        actor_ids=(character.id,),
                        visible_to=(character.id,),
                        tags=("project_seed",),
                    )
                )
            relationships = "; ".join(
                f"{relationship.character_id}: {relationship.description}"
                for relationship in character.relationships
            )
            actors.append(
                factory.build_actor(
                    default_character_recipe(
                        model_profile_id=f"actor:{character.id}",
                        content_locale=request.content_locale,
                    ),
                    actor_params={
                        "name": character.id,
                        "identity": character.identity,
                        "goal": character.current_goal or character.core_desire,
                        "relationships": relationships,
                    },
                    memory=memory,
                )
            )

        gm_id = "story-game-master"
        gm_memory = ConcordiaMemoryBank(
            owner_id=gm_id,
            scope=MemoryScope.GAME_MASTER,
        )
        gm_memory.add(
            MemoryRecord(
                record_id=f"seed:{session_id}:premise",
                record_type=MemoryRecordType.PREMISE,
                scope=MemoryScope.GAME_MASTER,
                owner_id=gm_id,
                session_id=session_id,
                branch_id=request.branch_id,
                step=0,
                text=request.premise_text,
                content_locale=request.content_locale,
                created_at=datetime.now(UTC),
                tags=("project_seed", "session_premise"),
            )
        )
        gm_memory.add(
            MemoryRecord(
                record_id=f"seed:{session_id}:world",
                record_type=MemoryRecordType.PREMISE,
                scope=MemoryScope.GAME_MASTER,
                owner_id=gm_id,
                session_id=session_id,
                branch_id=request.branch_id,
                step=0,
                text=json.dumps(
                    snapshot.world.model_dump(mode="json"),
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                content_locale=request.content_locale,
                created_at=datetime.now(UTC),
                tags=("project_seed", "world_state"),
            )
        )
        for fact in snapshot.facts:
            gm_memory.add(
                MemoryRecord(
                    record_id=f"seed:{fact.id}:gm",
                    record_type=MemoryRecordType.PREMISE,
                    scope=MemoryScope.GAME_MASTER,
                    owner_id=gm_id,
                    session_id=session_id,
                    branch_id=request.branch_id,
                    step=0,
                    text=fact.statement,
                    content_locale=request.content_locale,
                    created_at=fact.introduced_at,
                    actor_ids=fact.known_by,
                    tags=("project_seed", fact.visibility),
                )
            )
        game_master = factory.build_game_master(
            default_game_master_recipe(
                model_profile_id=gm_model_key,
                content_locale=request.content_locale,
            ),
            gm_params={
                "name": gm_id,
                "scene_goal": request.premise_text,
            },
            actors=tuple(actors),
            shared_memory=gm_memory,
        )
        runtime = StorySimulationRuntime(
            session_id=session_id,
            branch_id=request.branch_id,
            content_locale=request.content_locale,
            actors=tuple(actors),
            game_master=game_master,
            cancellation=cancellation,
            model_traces=model_traces,
            language_models=tuple(models.values()),
        )
        branches = BranchStore(self._projects_root / request.project_id)
        try:
            branch = branches.load(request.branch_id)
        except FileNotFoundError:
            return runtime
        if branch.head_checkpoint_id is None:
            return runtime
        restored = CheckpointStore(self._projects_root / request.project_id).load(
            branch.head_checkpoint_id
        )
        runtime.restore_states(
            actor_states=restored.actor_states,
            game_master_states=restored.game_master_states,
            memory_snapshots=restored.memory_snapshots,
        )
        runtime.set_content_locale(request.content_locale)
        runtime.initial_snapshot = restored
        return runtime
