import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from threading import Event

from pydantic import JsonValue

from story_engine.concordia_runtime.factory import (
    ConcordiaGameMasterActor,
    ConcordiaStoryActor,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.resolver import (
    ConcordiaResolverKernel,
    SimulationCancelledError,
)
from story_engine.concordia_runtime.roster import (
    MAX_SCENE_ROSTER_SIZE,
    ConcordiaRosterPlanner,
)
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.memory import MemorySnapshot
from story_engine.domain.models import Character
from story_engine.domain.projection import (
    EffectOperation,
    EventVisibility,
    ResolvedEvent,
    ResolvedTurn,
)
from story_engine.domain.recipe import PerceptionFrame
from story_engine.domain.simulation import (
    CharacterRef,
    PromotionDecision,
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
        characters: tuple[Character, ...] = (),
        pending_scene_events: tuple[ResolvedEvent, ...] = (),
        promoted_actor_builder: Callable[
            [Character, tuple[ResolvedEvent, ...]],
            tuple[ConcordiaStoryActor, object],
        ]
        | None = None,
        promotion_reviewer: Callable[
            [Character, tuple[ResolvedEvent, ...]], PromotionDecision
        ]
        | None = None,
        game_master_rebuilder: Callable[
            [tuple[ConcordiaStoryActor, ...], ConcordiaGameMasterActor],
            ConcordiaGameMasterActor,
        ]
        | None = None,
        available_actors: tuple[ConcordiaStoryActor, ...] = (),
        roster_planner: ConcordiaRosterPlanner | None = None,
        initial_roster_selected: bool = False,
    ) -> None:
        if not actors:
            raise ValueError("simulation runtime requires at least one actor")
        if len(actors) > MAX_SCENE_ROSTER_SIZE:
            raise ValueError(
                f"scene roster cannot exceed {MAX_SCENE_ROSTER_SIZE} active Agents"
            )
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
        self._stage_events: list[SimulationStageEvent] = []
        self._actors_by_name = {actor.name: actor for actor in actors}
        self._all_actors_by_name = {
            actor.name: actor for actor in (*actors, *available_actors)
        }
        self._characters_by_id = {character.id: character for character in characters}
        self._pending_scene_events = list(pending_scene_events)
        self._promoted_actor_builder = promoted_actor_builder
        self._promotion_reviewer = promotion_reviewer
        self._game_master_rebuilder = game_master_rebuilder
        self._roster_planner = roster_planner
        self._roster_planned = initial_snapshot is not None or initial_roster_selected
        if len(self._actors_by_name) != len(actors):
            raise ValueError("simulation actor IDs must be unique")

    def roster_actor_ids(self) -> tuple[str, ...]:
        return tuple(actor.name for actor in self.actors)

    def character_states(self) -> tuple[Character, ...]:
        return tuple(
            self._characters_by_id[key] for key in sorted(self._characters_by_id)
        )

    def pending_scene_events(self) -> tuple[ResolvedEvent, ...]:
        return tuple(self._pending_scene_events)

    def _apply_character_effects(self, resolved: ResolvedTurn) -> tuple[str, ...]:
        candidate_effects = (
            *resolved.effects,
            *(effect for event in resolved.events for effect in event.effects),
        )
        effects = tuple(
            {effect.effect_id: effect for effect in candidate_effects}.values()
        )
        changed: list[str] = []
        new_characters: dict[str, Character] = {}
        for effect in effects:
            if effect.operation != EffectOperation.CREATE_CHARACTER:
                continue
            if effect.target_id is None or not isinstance(effect.after, dict):
                raise ValueError("create_character requires a target and payload")
            if effect.target_id in self._characters_by_id:
                raise ValueError(
                    "GM attempted to recreate existing character "
                    f"{effect.target_id!r}; reference it through participant_ids "
                    "instead."
                )
            if effect.target_id in new_characters:
                raise ValueError(
                    "GM attempted to create character "
                    f"{effect.target_id!r} more than once in one resolution"
                )
            payload = dict(effect.after)
            character = Character(
                id=effect.target_id,
                display_name=str(payload.get("display_name") or effect.target_id),
                type="npc",
                identity=str(payload.get("identity") or ""),
                core_desire=str(payload.get("core_desire") or ""),
                location=(
                    str(payload["location"]) if payload.get("location") else None
                ),
            )
            new_characters[character.id] = character
            changed.append(f"npc-created:{character.id}")

        character_ids = {*self._characters_by_id, *new_characters}
        actor_ids = set(self._actors_by_name)
        for event in resolved.events:
            unknown_participants = set(event.participant_ids) - character_ids
            if unknown_participants:
                raise ValueError(
                    "event contains unregistered participant IDs: "
                    f"{sorted(unknown_participants)}"
                )
            unknown_observers = set(event.observer_ids) - actor_ids
            if unknown_observers:
                raise ValueError(
                    "event contains non-actor observer IDs: "
                    f"{sorted(unknown_observers)}"
                )
        self._characters_by_id.update(new_characters)
        return tuple(changed)

    def _evaluate_promotions(
        self,
        resolved: ResolvedTurn,
        *,
        stage_event_id: str | None,
    ) -> tuple[PromotionDecision, ...]:
        self._pending_scene_events.extend(resolved.events)
        if resolved.boundary.value == "none":
            return ()
        scene_events = tuple(self._pending_scene_events)
        candidates = tuple(
            character
            for character in self.character_states()
            if character.type == "npc"
            and any(character.id in event.participant_ids for event in scene_events)
        )
        if candidates and (
            self._promotion_reviewer is None or self._promoted_actor_builder is None
        ):
            raise ValueError("automatic NPC promotion is unavailable")
        decisions: list[PromotionDecision] = []
        for character in candidates:
            evidence = tuple(
                event for event in scene_events if character.id in event.participant_ids
            )
            assert self._promotion_reviewer is not None
            decision = self._promotion_reviewer(character, evidence)
            decisions.append(decision)
            if not decision.promote:
                continue
            promoted = Character.model_validate(
                character.model_copy(
                    update={
                        "type": "active",
                        "current_goal": decision.proposed_goal,
                        "version": character.version + 1,
                    }
                )
            )
            assert self._promoted_actor_builder is not None
            actor, model = self._promoted_actor_builder(promoted, evidence)
            self._characters_by_id[promoted.id] = promoted
            self._all_actors_by_name[actor.name] = actor
            self._language_models.append(model)
        self._pending_scene_events.clear()
        if self._roster_planner is not None:
            if stage_event_id is None:
                raise ValueError(
                    "promotion stage event is required for roster planning"
                )
            self._set_trace_context(
                step=resolved.step,
                component_ids=("game-master:roster-selection",),
                source_record_ids=tuple(event.event_id for event in scene_events),
                stage=SimulationStage.PROMOTION,
                task_label="下一场角色选择",
                stage_event_id=stage_event_id,
            )
            self._plan_next_roster(scene_events)
        return tuple(decisions)

    def _active_roster_candidates(self) -> dict[str, tuple[str, str]]:
        return {
            character.id: (
                character.display_name or character.id,
                (
                    f"{character.identity}; goal: "
                    f"{character.current_goal or character.core_desire}; "
                    f"location: {character.location or 'unknown'}"
                ),
            )
            for character in self.character_states()
            if character.type == "active"
            and character.id in self._all_actors_by_name
        }

    def _replace_roster(self, selected: tuple[str, ...]) -> bool:
        if not selected:
            raise ValueError("scene roster requires at least one active Agent")
        if len(selected) > MAX_SCENE_ROSTER_SIZE:
            raise ValueError(
                f"scene roster cannot exceed {MAX_SCENE_ROSTER_SIZE} active Agents"
            )
        unknown = set(selected) - set(self._all_actors_by_name)
        if unknown:
            raise ValueError(f"scene roster contains unknown Agents: {sorted(unknown)}")
        if selected == self.roster_actor_ids():
            return False
        if self._game_master_rebuilder is None:
            raise ValueError("Game Master roster rebuilder is unavailable")
        actors = tuple(self._all_actors_by_name[actor_id] for actor_id in selected)
        self.actors = actors
        self._actors_by_name = {actor.name: actor for actor in actors}
        self.game_master = self._game_master_rebuilder(actors, self.game_master)
        return True

    def _plan_initial_roster(self) -> tuple[str, ...]:
        if self._roster_planner is None or self._roster_planned:
            return ()
        selected = self._roster_planner.select_initial(
            self._active_roster_candidates()
        )
        self._replace_roster(selected)
        self._roster_planned = True
        return selected

    def _plan_next_roster(
        self,
        scene_events: tuple[ResolvedEvent, ...],
    ) -> tuple[str, ...]:
        if self._roster_planner is None:
            return ()
        selected = self._roster_planner.select_next(
            self._active_roster_candidates(),
            current_roster=self.roster_actor_ids(),
            scene_events=scene_events,
        )
        self._replace_roster(selected)
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
        stage: SimulationStage,
        task_label: str,
        stage_event_id: str,
    ) -> None:
        for model in self._language_models:
            setter = getattr(model, "set_trace_context", None)
            if setter is not None:
                setter(
                    step=step,
                    component_ids=component_ids,
                    source_record_ids=source_record_ids,
                    stage=stage.value,
                    task_label=task_label,
                    stage_event_id=stage_event_id,
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
            model_refs=tuple(
                dict.fromkeys(
                    trace.model_ref
                    for trace in stage_calls
                    if trace.model_ref is not None
                )
            ),
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
        stage_event = self._publish_stage(
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
                stage=current_stage,
                task_label="终止判断",
                stage_event_id=stage_event.event_id,
            )
            should_terminate, _ = self.game_master.should_terminate(
                session_id=self.session_id,
                step=step,
            )
            if should_terminate:
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
            stage_event = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
            )
            self._set_trace_context(
                step=step,
                component_ids=("game-master:roster-selection",),
                stage=current_stage,
                task_label="初始角色选择",
                stage_event_id=stage_event.event_id,
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
                    stage=current_stage,
                    task_label="角色观察",
                    stage_event_id=stage_event.event_id,
                )
                observation = self.game_master.make_observation(
                    actor,
                    session_id=self.session_id,
                    step=step,
                    content_locale=self.content_locale,
                ).observation_text
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
            stage_event = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                input_record_ids=tuple(observation_ids),
            )
            self._set_trace_context(
                step=step,
                component_ids=("game-master:actor-selection",),
                source_record_ids=tuple(observation_ids),
                stage=current_stage,
                task_label="行动角色选择",
                stage_event_id=stage_event.event_id,
            )
            actor_id = self.game_master.select_next_actor(
                self.actors,
                session_id=self.session_id,
                step=step,
            )
            actor = self._actors_by_name[actor_id]
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
            action_spec = self.game_master.create_action_spec(
                actor,
                session_id=self.session_id,
                step=step,
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
            stage_event = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                action_spec=action_spec,
                input_record_ids=actor_observation_ids,
            )
            self._set_trace_context(
                step=step,
                component_ids=(f"actor:{actor.name}:action",),
                source_record_ids=actor_observation_ids,
                stage=current_stage,
                task_label="角色行动",
                stage_event_id=stage_event.event_id,
            )
            action = actor.act(action_spec)
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
            stage_event = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                input_record_ids=(putative_id,),
            )
            self._set_trace_context(
                step=step,
                component_ids=("game-master:resolution",),
                source_record_ids=(putative_id,),
                stage=current_stage,
                task_label="世界结算",
                stage_event_id=stage_event.event_id,
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
                    existing_characters=tuple(
                        CharacterRef(
                            id=character.id,
                            display_name=character.display_name or character.id,
                            type=character.type,
                            location=character.location,
                        )
                        for character in self.character_states()
                    ),
                ),
                cancellation=self.cancellation,
            )
            event_id = f"event:{self.session_id}:{step}"
            character_effect_ids = self._apply_character_effects(resolved)
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                summary_text=resolved.raw_resolution_text,
                input_record_ids=(putative_id,),
                output_record_ids=(event_id, *character_effect_ids),
            )

            current_stage = SimulationStage.MEMORY_ROUTING
            stage_started = datetime.now(UTC)
            stage_event = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                input_record_ids=(event_id,),
            )
            self._set_trace_context(
                step=step,
                component_ids=("memory:routing",),
                source_record_ids=(event_id,),
                stage=current_stage,
                task_label="记忆路由",
                stage_event_id=stage_event.event_id,
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
                output_record_ids=tuple(routed_ids),
                visible_to=tuple(sorted(observer_ids)),
            )
            promotion_decisions: tuple[PromotionDecision, ...]
            if resolved.boundary.value == "none":
                promotion_decisions = self._evaluate_promotions(
                    resolved,
                    stage_event_id=None,
                )
            else:
                current_stage = SimulationStage.PROMOTION
                stage_started = datetime.now(UTC)
                stage_event = self._publish_stage(
                    step=step,
                    stage=current_stage,
                    status=StageStatus.RUNNING,
                    started_at=stage_started,
                    input_record_ids=(event_id,),
                )
                self._set_trace_context(
                    step=step,
                    component_ids=("editor:automatic-promotion",),
                    source_record_ids=tuple(
                        event.event_id for event in self._pending_scene_events
                    )
                    + tuple(event.event_id for event in resolved.events),
                    stage=current_stage,
                    task_label="NPC 晋升判断",
                    stage_event_id=stage_event.event_id,
                )
                promotion_decisions = self._evaluate_promotions(
                    resolved,
                    stage_event_id=stage_event.event_id,
                )
                self._publish_stage(
                    step=step,
                    stage=current_stage,
                    status=StageStatus.SUCCEEDED,
                    started_at=stage_started,
                    summary_text=(
                        "; ".join(
                            f"{decision.character_id}: "
                            f"{'promoted' if decision.promote else 'remains npc'}"
                            for decision in promotion_decisions
                        )
                        or "No NPC promotion candidates"
                    ),
                    input_record_ids=(event_id,),
                    output_record_ids=tuple(
                        f"promotion:{self.session_id}:{step}:{decision.character_id}"
                        for decision in promotion_decisions
                    ),
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
                promotion_decisions=promotion_decisions,
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
        for actor in self._all_actors_by_name.values():
            actor.memory.restore(memory_snapshots[actor.name])
            actor.set_state(actor_states[actor.name])
        self.game_master.memory.restore(memory_snapshots[self.game_master.name])
        self.game_master.set_state(game_master_states[self.game_master.name])
