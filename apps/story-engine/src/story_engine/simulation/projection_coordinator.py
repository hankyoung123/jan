"""Projection task scheduling kept outside the core simulation commands."""

from collections.abc import Callable
from threading import RLock

from story_engine.domain.projection import ProjectionKind, ProjectionTask
from story_engine.domain.simulation import StepResult, TurnSessionSnapshot
from story_engine.events.stream import EngineEventBus
from story_engine.simulation.projections import ProjectionTaskService

ProjectionServiceFactory = Callable[[str], ProjectionTaskService]


class SimulationProjectionCoordinator:
    """Cache per-project projection workers and isolate their failures."""

    def __init__(
        self,
        event_bus: EngineEventBus,
        projection_service_factory: ProjectionServiceFactory | None = None,
    ) -> None:
        self.event_bus = event_bus
        self._factory = projection_service_factory
        self._services: dict[str, ProjectionTaskService] = {}
        self._lock = RLock()

    @property
    def configured(self) -> bool:
        return self._factory is not None

    def _service(self, project_id: str) -> ProjectionTaskService:
        if self._factory is None:
            raise RuntimeError("projection tasks are not configured")
        with self._lock:
            service = self._services.get(project_id)
            if service is None:
                service = self._factory(project_id)
                self._services[project_id] = service
            return service

    def schedule(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> tuple[ProjectionTask, ...]:
        if self._factory is None:
            return ()
        try:
            return self._service(snapshot.project_id).enqueue_boundary(result, snapshot)
        except Exception as error:
            # The durable core Step is authoritative once committed. Projection
            # scheduling failures must never turn that completed Step into a failure.
            self.event_bus.publish(
                project_id=snapshot.project_id,
                subject_id=snapshot.session_id,
                event_type="simulation.stage.failed",
                payload={
                    "session_id": snapshot.session_id,
                    "stage": "projection_schedule",
                    "error": str(error),
                },
            )
            return ()

    def list(
        self,
        *,
        project_id: str,
        session_id: str,
    ) -> tuple[ProjectionTask, ...]:
        return self._service(project_id).list(
            project_id=project_id,
            session_id=session_id,
        )

    def retry(
        self,
        *,
        project_id: str,
        session_id: str,
        task_id: str,
    ) -> ProjectionTask:
        return self._service(project_id).retry(session_id=session_id, task_id=task_id)

    def rebuild(
        self,
        *,
        project_id: str,
        session_id: str,
        checkpoint_id: str,
        kind: ProjectionKind | None = None,
    ) -> tuple[ProjectionTask, ...]:
        return self._service(project_id).rebuild(
            project_id=project_id,
            session_id=session_id,
            checkpoint_id=checkpoint_id,
            kind=kind,
        )
