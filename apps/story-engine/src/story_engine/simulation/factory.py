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
from story_engine.concordia_runtime.memory import (
    ConcordiaMemoryBank,
    concordia_hash_embedder,
)
from story_engine.concordia_runtime.roster import (
    MAX_SCENE_ROSTER_SIZE,
    ConcordiaRosterPlanner,
)
from story_engine.domain.action import ActionSpecEnvelope
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.models import Character
from story_engine.domain.projection import ResolutionEnvelope, ResolvedEvent
from story_engine.domain.simulation import (
    PromotionDecision,
    TurnSessionRequest,
    TurnSessionSnapshot,
)
from story_engine.domain.trace import ModelCallTrace
from story_engine.models.gateway import ModelGateway
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.review.promotion import AutomaticPromotionReviewer
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
        self._embedder = embedder or concordia_hash_embedder

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
        characters = (
            restored.characters if restored is not None else snapshot.characters
        )
        active_characters = tuple(
            character for character in characters if character.type == "active"
        )
        if request.actor_ids:
            by_id = {character.id: character for character in active_characters}
            unknown = set(request.actor_ids) - set(by_id)
            if unknown:
                raise ValueError(f"unknown or inactive actor IDs: {sorted(unknown)}")
        if not active_characters:
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
                profile_resolver=lambda: "actor",
                session_id=session_id,
                branch_id=request.branch_id,
                actor_id=actor_id,
                cancellation=cancellation,
                trace_sink=record_trace,
            )
            models[key] = model
            return model

        for character in active_characters:
            create_actor_model(character.id)
        gm_model_key = "game-master"
        models[gm_model_key] = JanConcordiaLanguageModel(
            self._gateway,
            profile_id="game_master",
            task_type="game_master",
            content_locale=request.content_locale,
            profile_resolver=lambda: "game_master",
            session_id=session_id,
            branch_id=request.branch_id,
            cancellation=cancellation,
            trace_sink=record_trace,
        )
        gm_component_models = {
            "next_action_spec": JanConcordiaLanguageModel(
                self._gateway,
                profile_id="game_master",
                task_type="game_master",
                content_locale=request.content_locale,
                profile_resolver=lambda: "game_master",
                output_schema=json.dumps(
                    ActionSpecEnvelope.model_json_schema(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                session_id=session_id,
                branch_id=request.branch_id,
                cancellation=cancellation,
                trace_sink=record_trace,
            ),
            "resolution": JanConcordiaLanguageModel(
                self._gateway,
                profile_id="game_master",
                task_type="game_master",
                content_locale=request.content_locale,
                profile_resolver=lambda: "game_master",
                output_schema=json.dumps(
                    ResolutionEnvelope.model_json_schema(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                session_id=session_id,
                branch_id=request.branch_id,
                cancellation=cancellation,
                trace_sink=record_trace,
            ),
        }
        models.update(gm_component_models)
        promotion_model = JanConcordiaLanguageModel(
            self._gateway,
            profile_id="editor",
            task_type="editor",
            content_locale=request.content_locale,
            profile_resolver=lambda: "editor",
            output_schema=json.dumps(
                PromotionDecision.model_json_schema(),
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            session_id=session_id,
            branch_id=request.branch_id,
            cancellation=cancellation,
            trace_sink=record_trace,
        )
        models["promotion-reviewer"] = promotion_model
        promotion_reviewer = AutomaticPromotionReviewer(promotion_model)
        roster_model = JanConcordiaLanguageModel(
            self._gateway,
            profile_id="game_master",
            task_type="game_master",
            content_locale=request.content_locale,
            profile_resolver=lambda: "game_master",
            session_id=session_id,
            branch_id=request.branch_id,
            cancellation=cancellation,
            trace_sink=record_trace,
        )
        models["roster-planner"] = roster_model
        roster_planner = ConcordiaRosterPlanner(
            model=roster_model,
            premise_text=request.premise_text,
            content_locale=request.content_locale,
        )
        factory = ConcordiaActorFactory(models)
        actors: list[ConcordiaStoryActor] = []
        facts_by_id = {fact.id: fact for fact in snapshot.facts}
        public_fact_ids = set(snapshot.world.public_fact_ids)

        def build_character_actor(
            character: Character,
            evidence: tuple[ResolvedEvent, ...] = (),
        ) -> tuple[ConcordiaStoryActor, JanConcordiaLanguageModel]:
            key = f"actor:{character.id}"
            model = models.get(key)
            if model is None:
                model = create_actor_model(character.id)
                factory.register_model(key, model)
            memory = ConcordiaMemoryBank(
                owner_id=character.id,
                scope=MemoryScope.CHARACTER,
                embedder=self._embedder,
            )
            visible_fact_ids = public_fact_ids | set(character.known_fact_ids)
            for fact_id in sorted(visible_fact_ids):
                fact = facts_by_id.get(fact_id)
                if fact is None:
                    continue
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
            for event in evidence:
                memory.add(
                    MemoryRecord(
                        record_id=f"promotion-evidence:{event.event_id}:{character.id}",
                        record_type=MemoryRecordType.OBSERVATION,
                        scope=MemoryScope.CHARACTER,
                        owner_id=character.id,
                        session_id=session_id,
                        branch_id=request.branch_id,
                        step=event.step,
                        text=event.event_text,
                        content_locale=request.content_locale,
                        created_at=event.occurred_at,
                        actor_ids=event.participant_ids,
                        visible_to=(character.id,),
                        source_record_ids=(event.event_id,),
                        tags=("promotion_evidence",),
                    )
                )
            relationships = "; ".join(
                f"{relationship.character_id}: {relationship.description}"
                for relationship in character.relationships
            )
            actor = factory.build_actor(
                default_character_recipe(
                    model_profile_id=key,
                    content_locale=request.content_locale,
                ),
                actor_params={
                    "name": character.id,
                    "identity": character.identity,
                    "goal": character.current_goal or character.core_desire,
                    "relationships": relationships,
                    "project_root": str(project_root),
                    "branch_id": request.branch_id,
                },
                memory=memory,
            )
            return actor, model

        for character in active_characters:
            actor, _ = build_character_actor(character)
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
        if restored is not None and restored.roster_actor_ids:
            active_ids = set(restored.roster_actor_ids)
        elif request.actor_ids:
            active_ids = set(request.actor_ids)
        else:
            active_ids = {
                actor.name for actor in actors[:MAX_SCENE_ROSTER_SIZE]
            }
        active_actors = tuple(actor for actor in actors if actor.name in active_ids)
        game_master = factory.build_game_master(
            default_game_master_recipe(
                model_profile_id=gm_model_key,
                content_locale=request.content_locale,
            ),
            gm_params={
                "name": gm_id,
                "scene_goal": request.premise_text,
                "project_root": str(project_root),
                "branch_id": request.branch_id,
            },
            actors=active_actors,
            shared_memory=gm_memory,
            component_models=gm_component_models,
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
                    "project_root": str(project_root),
                    "branch_id": request.branch_id,
                },
                actors=current_actors,
                shared_memory=previous.memory,
                component_models=gm_component_models,
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
            characters=characters,
            pending_scene_events=(
                restored.pending_scene_events if restored is not None else ()
            ),
            promoted_actor_builder=build_character_actor,
            promotion_reviewer=promotion_reviewer.review,
            game_master_rebuilder=rebuild_game_master,
            available_actors=tuple(actors),
            roster_planner=roster_planner,
            initial_roster_selected=restored is not None or bool(request.actor_ids),
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
        from story_engine.wiki.store import WikiStore

        for instruction in WikiStore(
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
