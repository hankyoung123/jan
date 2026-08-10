"""Application facade for the core simulation state machine."""

from collections.abc import Callable
from threading import Event

from story_engine.domain.projection import ProjectionKind, ProjectionTask, ResolvedEvent
from story_engine.domain.session_manifest import SessionManifest
from story_engine.domain.simulation import (
    CommitResult,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
)
from story_engine.domain.trace import SimulationStageEvent
from story_engine.events.stream import EngineEventBus
from story_engine.persistence.command_store import CommandReceiptStore
from story_engine.simulation.command_service import SimulationCommandService
from story_engine.simulation.commands import SessionCommandCoordinator
from story_engine.simulation.engine import StoryTurnEngine
from story_engine.simulation.persistence import (
    CommitKernelFactory,
    SimulationPersistenceService,
)
from story_engine.simulation.projection_coordinator import (
    ProjectionServiceFactory,
    SimulationProjectionCoordinator,
)


class SimulationApplicationService:
    """Keep the public API stable while delegating each responsibility."""

    def __init__(
        self,
        engine: StoryTurnEngine,
        event_bus: EngineEventBus,
        commit_kernel_factory: CommitKernelFactory | None = None,
        projection_service_factory: ProjectionServiceFactory | None = None,
        command_coordinator: SessionCommandCoordinator | None = None,
        receipt_store_factory: Callable[[str], CommandReceiptStore] | None = None,
    ) -> None:
        self.engine = engine
        self.event_bus = event_bus
        self.persistence = SimulationPersistenceService(
            engine,
            event_bus,
            commit_kernel_factory,
        )
        self.projections = SimulationProjectionCoordinator(
            event_bus,
            projection_service_factory,
        )
        self.commands = SimulationCommandService(
            engine,
            self.persistence,
            self.projections,
            command_coordinator
            or SessionCommandCoordinator(receipt_store_factory=receipt_store_factory),
        )

    def publish(self, event: SimulationStageEvent) -> None:
        """Implement the runtime observer contract."""
        self.persistence.publish_stage(event)

    def start(self, request: TurnSessionRequest) -> TurnSessionSnapshot:
        return self.persistence.start(request, observer=self)

    def get(self, session_id: str) -> TurnSessionSnapshot:
        return self.engine.get(session_id)

    def current_scene_events(
        self,
        snapshot: TurnSessionSnapshot,
    ) -> tuple[ResolvedEvent, ...]:
        return self.persistence.current_scene_events(snapshot)

    def get_durable(
        self,
        project_id: str,
        session_id: str,
    ) -> TurnSessionSnapshot | SessionManifest:
        return self.persistence.get_durable(
            project_id,
            session_id,
            observer=self,
        )

    def list(self, project_id: str) -> tuple[SessionManifest, ...]:
        return self.persistence.list(project_id)

    def restore(
        self,
        project_id: str,
        *,
        checkpoint_id: str,
    ) -> TurnSessionSnapshot:
        return self.persistence.restore(
            project_id,
            checkpoint_id=checkpoint_id,
            observer=self,
        )

    def restore_branch(
        self,
        project_id: str,
        *,
        branch_id: str,
    ) -> TurnSessionSnapshot:
        return self.persistence.restore_branch(
            project_id,
            branch_id=branch_id,
            observer=self,
        )

    def step(
        self,
        session_id: str,
        *,
        command_id: str,
        expected_state_hash: str,
        cancellation: Event,
    ) -> StepResult:
        return self.commands.step(
            session_id,
            command_id=command_id,
            expected_state_hash=expected_state_hash,
            cancellation=cancellation,
        )

    def interactive_turn(
        self,
        session_id: str,
        *,
        text: str,
        command_id: str | None = None,
    ) -> StepResult:
        return self.commands.interactive_turn(
            session_id,
            text=text,
            command_id=command_id,
        )

    def resume_pending_interactive_handoff(
        self,
        snapshot: TurnSessionSnapshot,
    ) -> TurnSessionSnapshot:
        self.commands.resume_pending_interactive_handoff(snapshot)
        return self.engine.get(snapshot.session_id)

    def run(self, session_id: str, *, cancellation: Event) -> TurnSessionSnapshot:
        return self.commands.run(session_id, cancellation=cancellation)

    def run_in_background(
        self,
        session_id: str,
        *,
        command_id: str,
        expected_state_hash: str,
        resume: bool = False,
    ) -> TurnSessionSnapshot:
        return self.commands.run_in_background(
            session_id,
            command_id=command_id,
            expected_state_hash=expected_state_hash,
            resume=resume,
        )

    def pause(self, session_id: str) -> TurnSessionSnapshot:
        return self.commands.pause(session_id)

    def terminate(self, session_id: str, *, reason_text: str) -> TurnSessionSnapshot:
        return self.commands.terminate(session_id, reason_text=reason_text)

    def cancel(self, session_id: str, *, reason_text: str) -> TurnSessionSnapshot:
        return self.commands.cancel(session_id, reason_text=reason_text)

    def shutdown(self) -> None:
        self.commands.shutdown()

    def recover_projections(self, project_id: str) -> None:
        self.projections.recover(project_id)

    def checkpoint(self, session_id: str, *, reason: str) -> CommitResult:
        return self.persistence.checkpoint(session_id, reason=reason)

    def switch_locale(
        self,
        session_id: str,
        *,
        content_locale: str,
    ) -> TurnSessionSnapshot:
        return self.persistence.switch_locale(
            session_id,
            content_locale=content_locale,
        )

    def checkpoint_inactive_sessions(self) -> tuple[CommitResult, ...]:
        return self.persistence.checkpoint_inactive_sessions()

    def list_projections(
        self,
        *,
        project_id: str,
        session_id: str,
    ) -> tuple[ProjectionTask, ...]:
        return self.projections.list(project_id=project_id, session_id=session_id)

    def retry_projection(
        self,
        *,
        project_id: str,
        session_id: str,
        task_id: str,
    ) -> ProjectionTask:
        return self.projections.retry(
            project_id=project_id,
            session_id=session_id,
            task_id=task_id,
        )

    def rebuild_projections(
        self,
        *,
        project_id: str,
        session_id: str,
        kind: ProjectionKind | None = None,
    ) -> tuple[ProjectionTask, ...]:
        state = self.get_durable(project_id, session_id)
        checkpoint_id = (
            state.checkpoint_id
            if isinstance(state, TurnSessionSnapshot)
            else state.head_checkpoint_id
        )
        if checkpoint_id is None:
            raise ValueError("simulation has no durable checkpoint to rebuild")
        return self.projections.rebuild(
            project_id=project_id,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            kind=kind,
        )


__all__ = ["SimulationApplicationService"]
