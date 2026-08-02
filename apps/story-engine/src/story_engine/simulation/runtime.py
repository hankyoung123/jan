import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from threading import Event

from concordia.environment.engines import sequential  # type: ignore[import-untyped]
from pydantic import JsonValue

from story_engine.concordia_runtime.action_spec import ConcordiaActionSpecCodec
from story_engine.concordia_runtime.factory import (
    ConcordiaGameMasterActor,
    ConcordiaStoryActor,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.memory_lifecycle import ConcordiaMemoryLifecycle
from story_engine.concordia_runtime.resolver import (
    ConcordiaResolverKernel,
    SimulationCancelledError,
)
from story_engine.concordia_runtime.roster import ConcordiaRosterPlanner
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.memory import MemorySnapshot
from story_engine.domain.projection import (
    EffectOperation,
    EventVisibility,
    ResolvedTurn,
)
from story_engine.domain.recipe import PerceptionFrame
from story_engine.domain.simulation import (
    DynamicEntityDefinition,
    ResolverContext,
    StepResult,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import (
    ModelCallTrace,
    SimulationObserver,
    SimulationStage,
    SimulationStageEvent,
    StageStatus,
)


class StorySimulationRuntime:
    """One branch-local set of persistent Concordia entities and memories."""

    def __init__(
        self,
        *,
        project_id: str,
        session_id: str,
        branch_id: str,
        content_locale: str,
        actors: tuple[ConcordiaStoryActor, ...],
        game_master: ConcordiaGameMasterActor,
        resolver: ConcordiaResolverKernel | None = None,
        cancellation: Event | None = None,
        model_traces: list[ModelCallTrace] | None = None,
        initial_snapshot: TurnSessionSnapshot | None = None,
        language_models: Sequence[object] = (),
        observer: SimulationObserver | None = None,
        memory_lifecycle: ConcordiaMemoryLifecycle | None = None,
        allow_dynamic_entities: bool = False,
        dynamic_entities: tuple[DynamicEntityDefinition, ...] = (),
        dynamic_actor_builder: Callable[
            [DynamicEntityDefinition], tuple[ConcordiaStoryActor, object]
        ]
        | None = None,
        game_master_rebuilder: Callable[
            [tuple[ConcordiaStoryActor, ...], ConcordiaGameMasterActor],
            ConcordiaGameMasterActor,
        ]
        | None = None,
        available_actors: tuple[ConcordiaStoryActor, ...] = (),
        roster_planner: ConcordiaRosterPlanner | None = None,
    ) -> None:
        if not actors:
            raise ValueError("simulation runtime requires at least one actor")
        self.project_id = project_id
        self.session_id = session_id
        self.branch_id = branch_id
        self.content_locale = content_locale
        self.actors = actors
        self.game_master = game_master
        self.resolver = resolver or ConcordiaResolverKernel()
        self.cancellation = cancellation or Event()
        self._model_traces = model_traces if model_traces is not None else []
        self.initial_snapshot = initial_snapshot
        self._language_models = list(language_models)
        self._observer = observer
        self._memory_lifecycle = memory_lifecycle
        self._stage_events: list[SimulationStageEvent] = []
        self._sequential = sequential.Sequential()
        self._actors_by_name = {actor.name: actor for actor in actors}
        self._all_actors_by_name = {
            actor.name: actor for actor in (*actors, *available_actors)
        }
        self._allow_dynamic_entities = allow_dynamic_entities
        self._dynamic_entities = {
            definition.entity_id: definition for definition in dynamic_entities
        }
        self._dynamic_actor_builder = dynamic_actor_builder
        self._game_master_rebuilder = game_master_rebuilder
        self._roster_planner = roster_planner
        self._roster_planned = initial_snapshot is not None
        if len(self._actors_by_name) != len(actors):
            raise ValueError("simulation actor IDs must be unique")

    def active_entity_ids(self) -> tuple[str, ...]:
        return tuple(actor.name for actor in self.actors)

    def dynamic_entity_definitions(self) -> tuple[DynamicEntityDefinition, ...]:
        return tuple(
            self._dynamic_entities[key] for key in sorted(self._dynamic_entities)
        )

    def _apply_entity_effects(self, resolved: ResolvedTurn) -> tuple[str, ...]:
        candidate_effects = (
            *resolved.effects,
            *(effect for event in resolved.events for effect in event.effects),
        )
        effects = tuple(
            {effect.effect_id: effect for effect in candidate_effects}.values()
        )
        changed: list[str] = []
        roster_changed = False
        for effect in effects:
            if effect.operation not in {
                EffectOperation.CREATE_ENTITY,
                EffectOperation.ARCHIVE_ENTITY,
            }:
                continue
            if not self._allow_dynamic_entities:
                if effect.required:
                    raise ValueError(
                        "required dynamic entity effect is disabled by policy"
                    )
                continue
            if effect.operation == EffectOperation.CREATE_ENTITY:
                if effect.target_id in self._all_actors_by_name:
                    actor = self._all_actors_by_name[effect.target_id]
                    if actor.name not in self._actors_by_name:
                        self._actors_by_name[actor.name] = actor
                        self.actors = (*self.actors, actor)
                        changed.append(f"entity-joined:{actor.name}")
                        roster_changed = True
                    continue
                if self._dynamic_actor_builder is None or not isinstance(
                    effect.after, dict
                ):
                    if effect.required:
                        raise ValueError("dynamic entity builder is unavailable")
                    continue
                payload = dict(effect.after)
                if effect.target_id is not None:
                    payload.setdefault("entity_id", effect.target_id)
                definition = DynamicEntityDefinition.model_validate(payload)
                if definition.entity_id in self._all_actors_by_name:
                    continue
                actor, model = self._dynamic_actor_builder(definition)
                self._all_actors_by_name[actor.name] = actor
                self._actors_by_name[actor.name] = actor
                self.actors = (*self.actors, actor)
                self._language_models.append(model)
                self._dynamic_entities[definition.entity_id] = definition.model_copy(
                    update={"active": True}
                )
                changed.append(f"entity-created:{definition.entity_id}")
                roster_changed = True
            elif effect.target_id in self._actors_by_name:
                entity_id = effect.target_id
                if len(self.actors) == 1:
                    if effect.required:
                        raise ValueError("cannot archive the last active entity")
                    continue
                self.actors = tuple(
                    actor for actor in self.actors if actor.name != entity_id
                )
                self._actors_by_name.pop(entity_id, None)
                archived_definition = self._dynamic_entities.get(entity_id)
                if archived_definition is not None:
                    self._dynamic_entities[entity_id] = archived_definition.model_copy(
                        update={"active": False}
                    )
                changed.append(f"entity-archived:{entity_id}")
                roster_changed = True
        if roster_changed:
            if self._game_master_rebuilder is None:
                raise ValueError("Game Master roster rebuilder is unavailable")
            self.game_master = self._game_master_rebuilder(
                self.actors, self.game_master
            )
        return tuple(changed)

    def _plan_initial_roster(self) -> tuple[str, ...]:
        if self._roster_planner is None or self._roster_planned:
            return ()
        selected = self._roster_planner.select_initial()
        actors = tuple(self._all_actors_by_name[actor_id] for actor_id in selected)
        if self._game_master_rebuilder is None:
            raise ValueError("Game Master roster rebuilder is unavailable")
        self.actors = actors
        self._actors_by_name = {actor.name: actor for actor in actors}
        self.game_master = self._game_master_rebuilder(actors, self.game_master)
        self._roster_planned = True
        return selected

    def _cancelled(self, external: Event) -> bool:
        return self.cancellation.is_set() or external.is_set()

    def _check_cancelled(self, external: Event) -> None:
        if self._cancelled(external):
            self.cancellation.set()
            raise SimulationCancelledError("simulation was cancelled")

    def set_observer(self, observer: SimulationObserver) -> None:
        self._observer = observer

    def _set_trace_context(
        self,
        *,
        step: int,
        component_ids: tuple[str, ...],
        source_record_ids: tuple[str, ...] = (),
    ) -> None:
        for model in self._language_models:
            setter = getattr(model, "set_trace_context", None)
            if setter is not None:
                setter(
                    step=step,
                    component_ids=component_ids,
                    source_record_ids=source_record_ids,
                )

    def _publish_stage(
        self,
        *,
        step: int,
        stage: SimulationStage,
        status: StageStatus,
        started_at: datetime,
        actor_id: str | None = None,
        action_spec: ActionSpec | None = None,
        summary_text: str | None = None,
        input_record_ids: tuple[str, ...] = (),
        output_record_ids: tuple[str, ...] = (),
        visible_to: tuple[str, ...] = (),
        completed_at: datetime | None = None,
        error_code: str | None = None,
    ) -> SimulationStageEvent:
        finished = completed_at or (
            datetime.now(UTC) if status != StageStatus.RUNNING else None
        )
        duration_ms = (
            max(0, int((finished - started_at).total_seconds() * 1000))
            if finished is not None
            else None
        )
        stage_calls = (
            tuple(
                trace
                for trace in self._model_traces
                if trace.step == step
                and trace.started_at >= started_at
                and (finished is None or trace.started_at <= finished)
            )
            if finished is not None
            else ()
        )
        event = SimulationStageEvent(
            event_id=f"stage-event:{uuid.uuid4().hex}",
            project_id=self.project_id,
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            stage=stage,
            status=status,
            actor_id=actor_id,
            action_spec=action_spec,
            summary_text=summary_text,
            input_record_ids=input_record_ids,
            output_record_ids=output_record_ids,
            visible_to=visible_to,
            profile_ids=tuple(dict.fromkeys(trace.profile_id for trace in stage_calls)),
            provider_ids=tuple(
                dict.fromkeys(trace.provider_id for trace in stage_calls)
            ),
            model_ids=tuple(dict.fromkeys(trace.model_id for trace in stage_calls)),
            prompt_tokens=sum(trace.prompt_tokens for trace in stage_calls),
            completion_tokens=sum(trace.completion_tokens for trace in stage_calls),
            duration_ms=duration_ms,
            error_code=error_code,
            started_at=started_at,
            completed_at=finished,
        )
        self._stage_events.append(event)
        if self._observer is not None:
            self._observer.publish(event)
        return event

    def execute_step(self, step: int, *, cancellation: Event) -> StepResult:
        current_stage = SimulationStage.TERMINATION
        stage_started = datetime.now(UTC)
        self._publish_stage(
            step=step,
            stage=current_stage,
            status=StageStatus.RUNNING,
            started_at=stage_started,
        )
        try:
            self._check_cancelled(cancellation)
            self._set_trace_context(
                step=step,
                component_ids=("game-master:termination",),
            )
            if self._sequential.terminate(self.game_master.entity):
                self._publish_stage(
                    step=step,
                    stage=current_stage,
                    status=StageStatus.SUCCEEDED,
                    started_at=stage_started,
                    summary_text="Game Master ended the session",
                )
                return StepResult(
                    session_id=self.session_id,
                    branch_id=self.branch_id,
                    step=step,
                    acting_actor_id=None,
                    action_spec=None,
                    action_text=None,
                    resolved_turn=None,
                    status=TurnSessionStatus.TERMINATED,
                )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                summary_text="Simulation continues",
            )

            current_stage = SimulationStage.OBSERVATION
            stage_started = datetime.now(UTC)
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
            )
            self._set_trace_context(
                step=step,
                component_ids=("game-master:roster-selection",),
            )
            selected_roster = self._plan_initial_roster()
            observation_ids: list[str] = []
            observation_summaries: list[str] = []
            for actor in self.actors:
                self._check_cancelled(cancellation)
                record_id = f"observation:{self.session_id}:{step}:{actor.name}"
                self._set_trace_context(
                    step=step,
                    component_ids=("game-master:observation",),
                    source_record_ids=(record_id,),
                )
                observation = self._sequential.make_observation(
                    self.game_master.entity,
                    actor.entity,
                )
                if observation.strip():
                    observation_ids.append(record_id)
                    observation_summaries.append(f"{actor.name}: {observation}")
                    actor.observe(
                        PerceptionFrame(
                            frame_id=record_id,
                            session_id=self.session_id,
                            branch_id=self.branch_id,
                            actor_id=actor.name,
                            step=step,
                            content_locale=self.content_locale,
                            observation_text=observation,
                        )
                    )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                summary_text=(
                    (
                        f"Initial roster: {', '.join(selected_roster)}\n"
                        if selected_roster
                        else ""
                    )
                    + ("\n".join(observation_summaries) or "No new observations")
                ),
                output_record_ids=tuple(observation_ids),
                visible_to=tuple(actor.name for actor in self.actors),
            )

            self._check_cancelled(cancellation)
            current_stage = SimulationStage.ACTOR_SELECTION
            stage_started = datetime.now(UTC)
            self._set_trace_context(
                step=step,
                component_ids=("game-master:actor-selection",),
                source_record_ids=tuple(observation_ids),
            )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                input_record_ids=tuple(observation_ids),
            )
            raw_actor, raw_spec = self._sequential.next_acting(
                self.game_master.entity,
                tuple(actor.entity for actor in self.actors),
            )
            actor = self._actors_by_name[raw_actor.name]
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                summary_text=f"{actor.name} selected as the next actor",
                input_record_ids=tuple(observation_ids),
            )

            current_stage = SimulationStage.ACTION_SPEC
            stage_started = datetime.now(UTC)
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
            )
            action_spec = ConcordiaActionSpecCodec.from_concordia(
                raw_spec,
                spec_id=f"action:{self.session_id}:{step}",
                content_locale=self.content_locale,
            )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                action_spec=action_spec,
                summary_text=action_spec.call_to_action,
            )
            if action_spec.output_type == ActionOutputType.SKIP_THIS_STEP:
                return StepResult(
                    session_id=self.session_id,
                    branch_id=self.branch_id,
                    step=step,
                    acting_actor_id=actor.name,
                    action_spec=action_spec,
                    action_text=None,
                    resolved_turn=None,
                    status=TurnSessionStatus.RUNNING,
                )

            current_stage = SimulationStage.ACTOR_ACTION
            stage_started = datetime.now(UTC)
            actor_observation_ids = tuple(
                record_id
                for record_id in observation_ids
                if record_id.endswith(f":{actor.name}")
            )
            self._set_trace_context(
                step=step,
                component_ids=(f"actor:{actor.name}:action",),
                source_record_ids=actor_observation_ids,
            )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                action_spec=action_spec,
                input_record_ids=actor_observation_ids,
            )
            action = raw_actor.act(raw_spec)
            putative_id = f"putative:{self.session_id}:{step}"
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                action_spec=action_spec,
                summary_text=action,
                input_record_ids=actor_observation_ids,
                output_record_ids=(putative_id,),
                visible_to=(actor.name,),
            )

            self._check_cancelled(cancellation)
            current_stage = SimulationStage.RESOLUTION
            stage_started = datetime.now(UTC)
            self._set_trace_context(
                step=step,
                component_ids=("game-master:resolution",),
                source_record_ids=(putative_id,),
            )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                input_record_ids=(putative_id,),
            )
            resolved = self.resolver.resolve(
                self.game_master,
                ResolverContext(
                    session_id=self.session_id,
                    branch_id=self.branch_id,
                    step=step,
                    acting_actor_id=actor.name,
                    putative_event_text=action,
                    content_locale=self.content_locale,
                ),
                cancellation=self.cancellation,
            )
            event_id = f"event:{self.session_id}:{step}"
            entity_effect_ids = self._apply_entity_effects(resolved)
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                summary_text=resolved.raw_resolution_text,
                input_record_ids=(putative_id,),
                output_record_ids=(event_id, *entity_effect_ids),
            )

            current_stage = SimulationStage.MEMORY_ROUTING
            stage_started = datetime.now(UTC)
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                input_record_ids=(event_id,),
            )
            self._set_trace_context(
                step=step,
                component_ids=(
                    "memory:routing",
                    "memory:reflection",
                    "memory:consolidation",
                ),
                source_record_ids=(event_id,),
            )
            observer_ids: set[str] = set() if resolved.events else {actor.name}
            for event in resolved.events:
                if event.visibility == EventVisibility.PUBLIC:
                    observer_ids.update(self._actors_by_name)
                elif event.visibility == EventVisibility.PARTICIPANTS:
                    observer_ids.update(event.participant_ids)
                elif event.visibility == EventVisibility.RESTRICTED:
                    observer_ids.update(event.observer_ids)
            observer_ids.intersection_update(self._actors_by_name)
            routed_ids: list[str] = []
            for observer_id in sorted(observer_ids):
                routed_id = f"event-observation:{self.session_id}:{step}:{observer_id}"
                self._actors_by_name[observer_id].observe(
                    PerceptionFrame(
                        frame_id=routed_id,
                        session_id=self.session_id,
                        branch_id=self.branch_id,
                        actor_id=observer_id,
                        step=step,
                        content_locale=self.content_locale,
                        observation_text=resolved.raw_resolution_text,
                        source_record_ids=(event_id,),
                    )
                )
                routed_ids.append(routed_id)
            lifecycle_records = (
                self._memory_lifecycle.process_boundary(
                    session_id=self.session_id,
                    branch_id=self.branch_id,
                    step=step,
                    boundary=resolved.boundary,
                    acting_actor=actor,
                    game_master=self.game_master,
                )
                if self._memory_lifecycle is not None
                else ()
            )
            lifecycle_ids = tuple(record.record_id for record in lifecycle_records)
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                summary_text=(
                    "World result routed to " + ", ".join(sorted(observer_ids))
                ),
                input_record_ids=(event_id,),
                output_record_ids=(*routed_ids, *lifecycle_ids),
                visible_to=tuple(sorted(observer_ids)),
            )
            return StepResult(
                session_id=self.session_id,
                branch_id=self.branch_id,
                step=step,
                acting_actor_id=actor.name,
                action_spec=action_spec,
                action_text=action,
                resolved_turn=resolved,
                status=TurnSessionStatus.RUNNING,
                boundary=resolved.boundary,
            )
        except Exception as error:
            status = (
                StageStatus.CANCELLED
                if isinstance(error, SimulationCancelledError)
                else StageStatus.FAILED
            )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=status,
                started_at=stage_started,
                summary_text=str(error),
                error_code=getattr(error, "code", type(error).__name__.lower()),
            )
            raise

    def actor_states(self) -> dict[str, dict[str, JsonValue]]:
        return {
            actor.name: actor.get_state() for actor in self._all_actors_by_name.values()
        }

    def game_master_states(self) -> dict[str, dict[str, JsonValue]]:
        return {self.game_master.name: self.game_master.get_state()}

    def memory_snapshots(self) -> dict[str, MemorySnapshot]:
        memories: dict[str, ConcordiaMemoryBank] = {
            actor.memory.owner_id: actor.memory
            for actor in self._all_actors_by_name.values()
        }
        memories[self.game_master.memory.owner_id] = self.game_master.memory
        return {owner: memory.snapshot() for owner, memory in memories.items()}

    def drain_model_traces(self) -> tuple[ModelCallTrace, ...]:
        traces = tuple(self._model_traces)
        self._model_traces.clear()
        return traces

    def drain_stage_events(self) -> tuple[SimulationStageEvent, ...]:
        events = tuple(self._stage_events)
        self._stage_events.clear()
        return events

    def set_content_locale(self, content_locale: str) -> None:
        for actor in self._all_actors_by_name.values():
            actor.set_content_locale(content_locale)
        self.game_master.set_content_locale(content_locale)
        if self._memory_lifecycle is not None:
            self._memory_lifecycle.set_content_locale(content_locale)
        for model in self._language_models:
            setter = getattr(model, "set_content_locale", None)
            if setter is not None:
                setter(content_locale)
        self.content_locale = content_locale

    def restore_states(
        self,
        *,
        actor_states: Mapping[str, Mapping[str, JsonValue]],
        game_master_states: Mapping[str, Mapping[str, JsonValue]],
        memory_snapshots: Mapping[str, MemorySnapshot],
    ) -> None:
        for actor in self.actors:
            actor.memory.restore(memory_snapshots[actor.name])
            actor.set_state(actor_states[actor.name])
        self.game_master.memory.restore(memory_snapshots[self.game_master.name])
        self.game_master.set_state(game_master_states[self.game_master.name])
