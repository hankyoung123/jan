import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import numpy as np

from story_engine.concordia_runtime.factory import (
    ConcordiaActorFactory,
    ConcordiaGameMasterActor,
    ConcordiaStoryActor,
    default_character_recipe,
    default_game_master_recipe,
)
from story_engine.concordia_runtime.language_model import JanConcordiaLanguageModel
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.memory_lifecycle import ConcordiaMemoryLifecycle
from story_engine.concordia_runtime.roster import ConcordiaRosterPlanner
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.simulation import (
    DynamicEntityDefinition,
    TurnSessionRequest,
    TurnSessionSnapshot,
)
from story_engine.domain.trace import ModelCallTrace
from story_engine.models.gateway import ModelGateway
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.projection.world_bible import WorldBibleStore
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
        embedder: Callable[[str], np.ndarray] | None = None,
    ) -> None:
        self._projects_root = projects_root
        self._gateway = gateway
        self._trace_sink = trace_sink
        self._embedder = embedder or (
            lambda text: np.asarray(self._gateway.embed(text), dtype=float)
        )

    def __call__(
        self,
        session_id: str,
        request: TurnSessionRequest,
    ) -> StorySimulationRuntime:
        return self._build(session_id, request, restore_snapshot=None)

    def from_snapshot(
        self,
        session_id: str,
        request: TurnSessionRequest,
        snapshot: TurnSessionSnapshot,
    ) -> StorySimulationRuntime:
        return self._build(session_id, request, restore_snapshot=snapshot)

    def _build(
        self,
        session_id: str,
        request: TurnSessionRequest,
        *,
        restore_snapshot: TurnSessionSnapshot | None,
    ) -> StorySimulationRuntime:
        project_root = self._projects_root / request.project_id
        restored = restore_snapshot
        branches = BranchStore(project_root)
        if restored is None:
            try:
                branch = branches.load(request.branch_id)
                if branch.head_checkpoint_id is not None:
                    restored = CheckpointStore(project_root).load(
                        branch.head_checkpoint_id
                    )
            except FileNotFoundError:
                pass
        snapshot = ProjectStore(self._projects_root / request.project_id).load()
        project_actors = tuple(
            character for character in snapshot.characters if character.type == "active"
        )
        if request.actor_ids:
            by_id = {character.id: character for character in project_actors}
            unknown = set(request.actor_ids) - set(by_id)
            if unknown:
                raise ValueError(f"unknown or inactive actor IDs: {sorted(unknown)}")
        if not project_actors:
            raise ValueError("simulation requires at least one active character")

        cancellation = Event()
        model_traces: list[ModelCallTrace] = []

        def record_trace(trace: ModelCallTrace) -> None:
            model_traces.append(trace)
            if self._trace_sink is not None:
                self._trace_sink(trace)

        models: dict[str, JanConcordiaLanguageModel] = {}

        def create_actor_model(actor_id: str) -> JanConcordiaLanguageModel:
            key = f"actor:{actor_id}"
            model = JanConcordiaLanguageModel(
                self._gateway,
                profile_id="actor",
                task_type="actor",
                content_locale=request.content_locale,
                session_id=session_id,
                branch_id=request.branch_id,
                actor_id=actor_id,
                cancellation=cancellation,
                trace_sink=record_trace,
            )
            models[key] = model
            return model

        for character in project_actors:
            create_actor_model(character.id)
        for definition in restored.dynamic_entities if restored is not None else ():
            create_actor_model(definition.entity_id)
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
        reflection_model = JanConcordiaLanguageModel(
            self._gateway,
            profile_id="reflection",
            task_type="reflection",
            content_locale=request.content_locale,
            session_id=session_id,
            branch_id=request.branch_id,
            cancellation=cancellation,
            trace_sink=record_trace,
        )
        consolidation_model = JanConcordiaLanguageModel(
            self._gateway,
            profile_id="memory-consolidation",
            task_type="memory_consolidation",
            content_locale=request.content_locale,
            session_id=session_id,
            branch_id=request.branch_id,
            cancellation=cancellation,
            trace_sink=record_trace,
        )
        models["reflection"] = reflection_model
        models["memory-consolidation"] = consolidation_model
        roster_planner = None
        if not request.actor_ids and restored is None:
            actor_ids = [character.id for character in project_actors]
            roster_schema = json.dumps(
                {
                    "type": "object",
                    "required": ["actor_ids"],
                    "properties": {
                        "actor_ids": {
                            "type": "array",
                            "minItems": 1,
                            "uniqueItems": True,
                            "items": {"enum": actor_ids},
                        }
                    },
                    "additionalProperties": False,
                },
                separators=(",", ":"),
            )
            roster_model = JanConcordiaLanguageModel(
                self._gateway,
                profile_id="game-master",
                task_type="game_master",
                content_locale=request.content_locale,
                output_schema=roster_schema,
                session_id=session_id,
                branch_id=request.branch_id,
                cancellation=cancellation,
                trace_sink=record_trace,
            )
            models["roster-planner"] = roster_model
            roster_planner = ConcordiaRosterPlanner(
                model=roster_model,
                premise_text=request.premise_text,
                candidates={
                    character.id: (
                        f"{character.identity}; goal: "
                        f"{character.current_goal or character.core_desire}; "
                        f"location: {character.location or 'unknown'}"
                    )
                    for character in project_actors
                },
                content_locale=request.content_locale,
            )
        factory = ConcordiaActorFactory(models)
        actors: list[ConcordiaStoryActor] = []
        facts_by_id = {fact.id: fact for fact in snapshot.facts}
        public_fact_ids = set(snapshot.world.public_fact_ids)
        for character in project_actors:
            memory = ConcordiaMemoryBank(
                owner_id=character.id,
                scope=MemoryScope.CHARACTER,
                embedder=self._embedder,
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

        def build_dynamic_actor(
            definition: DynamicEntityDefinition,
        ) -> tuple[ConcordiaStoryActor, JanConcordiaLanguageModel]:
            key = f"actor:{definition.entity_id}"
            model = models.get(key)
            if model is None:
                model = create_actor_model(definition.entity_id)
                factory.register_model(key, model)
            memory = ConcordiaMemoryBank(
                owner_id=definition.entity_id,
                scope=MemoryScope.CHARACTER,
                embedder=self._embedder,
            )
            memory.add(
                MemoryRecord(
                    record_id=f"seed:{session_id}:dynamic:{definition.entity_id}",
                    record_type=MemoryRecordType.PREMISE,
                    scope=MemoryScope.CHARACTER,
                    owner_id=definition.entity_id,
                    session_id=session_id,
                    branch_id=request.branch_id,
                    step=0,
                    text=(
                        f"{definition.identity}\nGoal: {definition.goal}\n"
                        f"Location: {definition.location or 'unknown'}"
                    ),
                    content_locale=request.content_locale,
                    created_at=datetime.now(UTC),
                    actor_ids=(definition.entity_id,),
                    visible_to=(definition.entity_id,),
                    tags=("dynamic_entity",),
                )
            )
            actor = factory.build_actor(
                default_character_recipe(
                    model_profile_id=key,
                    content_locale=request.content_locale,
                ),
                actor_params={
                    "name": definition.entity_id,
                    "identity": definition.identity,
                    "goal": definition.goal,
                    "relationships": "",
                },
                memory=memory,
            )
            return actor, model

        dynamic_definitions = restored.dynamic_entities if restored is not None else ()
        for definition in dynamic_definitions:
            actor, _ = build_dynamic_actor(definition)
            actors.append(actor)

        gm_id = "story-game-master"
        gm_memory = ConcordiaMemoryBank(
            owner_id=gm_id,
            scope=MemoryScope.GAME_MASTER,
            embedder=self._embedder,
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
        if restored is not None and restored.active_entity_ids:
            active_ids = set(restored.active_entity_ids)
        elif request.actor_ids:
            active_ids = set(request.actor_ids)
        else:
            active_ids = {actor.name for actor in actors}
        active_actors = tuple(actor for actor in actors if actor.name in active_ids)
        game_master = factory.build_game_master(
            default_game_master_recipe(
                model_profile_id=gm_model_key,
                content_locale=request.content_locale,
            ),
            gm_params={
                "name": gm_id,
                "scene_goal": request.premise_text,
            },
            actors=active_actors,
            shared_memory=gm_memory,
        )

        def rebuild_game_master(
            current_actors: tuple[ConcordiaStoryActor, ...],
            previous: ConcordiaGameMasterActor,
        ) -> ConcordiaGameMasterActor:
            rebuilt_factory = ConcordiaActorFactory(models)
            return rebuilt_factory.build_game_master(
                default_game_master_recipe(
                    model_profile_id=gm_model_key,
                    content_locale=request.content_locale,
                ),
                gm_params={
                    "name": gm_id,
                    "scene_goal": request.premise_text,
                },
                actors=current_actors,
                shared_memory=previous.memory,
            )

        runtime = StorySimulationRuntime(
            project_id=request.project_id,
            session_id=session_id,
            branch_id=request.branch_id,
            content_locale=request.content_locale,
            actors=active_actors,
            game_master=game_master,
            cancellation=cancellation,
            model_traces=model_traces,
            language_models=tuple(models.values()),
            memory_lifecycle=ConcordiaMemoryLifecycle(
                reflection_model=reflection_model,
                consolidation_model=consolidation_model,
                content_locale=request.content_locale,
            ),
            allow_dynamic_entities=request.control.allow_dynamic_entities,
            dynamic_entities=dynamic_definitions,
            dynamic_actor_builder=build_dynamic_actor,
            game_master_rebuilder=rebuild_game_master,
            available_actors=tuple(actors),
            roster_planner=roster_planner,
        )
        if restored is not None:
            runtime.restore_states(
                actor_states=restored.actor_states,
                game_master_states=restored.game_master_states,
                memory_snapshots=restored.memory_snapshots,
            )
            runtime.set_content_locale(request.content_locale)
            runtime.initial_snapshot = restored
        existing_memory_ids = {
            record.record_id
            for record in runtime.game_master.memory.scan(lambda _record: True)
        }
        for instruction in WorldBibleStore(
            project_root,
            request.branch_id,
        ).list_instructions():
            if instruction.instruction_id in existing_memory_ids:
                continue
            runtime.game_master.memory.add(
                MemoryRecord(
                    record_id=instruction.instruction_id,
                    record_type=MemoryRecordType.SYSTEM,
                    scope=MemoryScope.GAME_MASTER,
                    owner_id=runtime.game_master.name,
                    session_id=session_id,
                    branch_id=request.branch_id,
                    step=restored.current_step if restored is not None else 0,
                    text=instruction.text,
                    content_locale=request.content_locale,
                    created_at=instruction.created_at,
                    source_record_ids=(instruction.applies_from_checkpoint_id,),
                    tags=("director_instruction",),
                    importance=1,
                )
            )
        return runtime
