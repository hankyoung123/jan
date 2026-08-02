from collections.abc import Mapping, Sequence
from threading import Event

from concordia.environment.engines import sequential  # type: ignore[import-untyped]
from pydantic import JsonValue

from story_engine.concordia_runtime.action_spec import ConcordiaActionSpecCodec
from story_engine.concordia_runtime.factory import (
    ConcordiaGameMasterActor,
    ConcordiaStoryActor,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.resolver import (
    ConcordiaResolverKernel,
    SimulationCancelledError,
)
from story_engine.domain.action import ActionOutputType
from story_engine.domain.memory import MemorySnapshot
from story_engine.domain.recipe import PerceptionFrame
from story_engine.domain.simulation import (
    ResolverContext,
    StepResult,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import ModelCallTrace


class StorySimulationRuntime:
    """One branch-local set of persistent Concordia entities and memories."""

    def __init__(
        self,
        *,
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
    ) -> None:
        if not actors:
            raise ValueError("simulation runtime requires at least one actor")
        self.session_id = session_id
        self.branch_id = branch_id
        self.content_locale = content_locale
        self.actors = actors
        self.game_master = game_master
        self.resolver = resolver or ConcordiaResolverKernel()
        self.cancellation = cancellation or Event()
        self._model_traces = model_traces if model_traces is not None else []
        self.initial_snapshot = initial_snapshot
        self._language_models = tuple(language_models)
        self._sequential = sequential.Sequential()
        self._actors_by_name = {actor.name: actor for actor in actors}
        if len(self._actors_by_name) != len(actors):
            raise ValueError("simulation actor IDs must be unique")

    def _cancelled(self, external: Event) -> bool:
        return self.cancellation.is_set() or external.is_set()

    def _check_cancelled(self, external: Event) -> None:
        if self._cancelled(external):
            self.cancellation.set()
            raise SimulationCancelledError("simulation was cancelled")

    def execute_step(self, step: int, *, cancellation: Event) -> StepResult:
        self._check_cancelled(cancellation)
        if self._sequential.terminate(self.game_master.entity):
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

        for actor in self.actors:
            self._check_cancelled(cancellation)
            observation = self._sequential.make_observation(
                self.game_master.entity,
                actor.entity,
            )
            if observation.strip():
                actor.observe(
                    PerceptionFrame(
                        frame_id=(f"observation:{self.session_id}:{step}:{actor.name}"),
                        session_id=self.session_id,
                        branch_id=self.branch_id,
                        actor_id=actor.name,
                        step=step,
                        content_locale=self.content_locale,
                        observation_text=observation,
                    )
                )

        self._check_cancelled(cancellation)
        raw_actor, raw_spec = self._sequential.next_acting(
            self.game_master.entity,
            tuple(actor.entity for actor in self.actors),
        )
        actor = self._actors_by_name[raw_actor.name]
        action_spec = ConcordiaActionSpecCodec.from_concordia(
            raw_spec,
            spec_id=f"action:{self.session_id}:{step}",
            content_locale=self.content_locale,
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

        action = raw_actor.act(raw_spec)
        self._check_cancelled(cancellation)
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
        return StepResult(
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            acting_actor_id=actor.name,
            action_spec=action_spec,
            action_text=action,
            resolved_turn=resolved,
            status=TurnSessionStatus.RUNNING,
        )

    def actor_states(self) -> dict[str, dict[str, JsonValue]]:
        return {actor.name: actor.get_state() for actor in self.actors}

    def game_master_states(self) -> dict[str, dict[str, JsonValue]]:
        return {self.game_master.name: self.game_master.get_state()}

    def memory_snapshots(self) -> dict[str, MemorySnapshot]:
        memories: dict[str, ConcordiaMemoryBank] = {
            actor.memory.owner_id: actor.memory for actor in self.actors
        }
        memories[self.game_master.memory.owner_id] = self.game_master.memory
        return {owner: memory.snapshot() for owner, memory in memories.items()}

    def drain_model_traces(self) -> tuple[ModelCallTrace, ...]:
        traces = tuple(self._model_traces)
        self._model_traces.clear()
        return traces

    def set_content_locale(self, content_locale: str) -> None:
        for actor in self.actors:
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
        for actor in self.actors:
            actor.memory.restore(memory_snapshots[actor.name])
            actor.set_state(actor_states[actor.name])
        self.game_master.memory.restore(memory_snapshots[self.game_master.name])
        self.game_master.set_state(game_master_states[self.game_master.name])
