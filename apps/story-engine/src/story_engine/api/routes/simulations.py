import asyncio
import re
import uuid
from contextlib import suppress
from pathlib import Path
from threading import Event

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from story_engine.api.model_errors import model_http_error
from story_engine.config import EngineSettings
from story_engine.domain.base import Identifier, LocaleCode, RuntimeModel
from story_engine.domain.projection import ProjectionKind, ProjectionTask
from story_engine.domain.session_manifest import SessionManifest
from story_engine.domain.simulation import (
    BranchManifest,
    CommitResult,
    ControlMode,
    ControlPolicy,
    OutputPolicy,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
)
from story_engine.events.stream import EngineEvent
from story_engine.models.errors import ModelGatewayError
from story_engine.models.gateway import ModelGateway
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.persistence.simulation_log import SimulationLogRecord
from story_engine.simulation.commands import SessionCommandConflictError
from story_engine.simulation.engine import (
    InvalidSessionTransitionError,
    SessionNotFoundError,
)
from story_engine.simulation.execution import BranchAlreadyActiveError
from story_engine.simulation.perception import (
    CheckpointTimelineEntry,
    InteractiveTurnResponse,
    PerceptionBuilder,
)
from story_engine.simulation.service import SimulationApplicationService
from story_engine.submission.project import SubmissionService
from story_engine.submission.service import last_ferry_before_submission
from story_engine.wiki.boundary import WikiBoundaryProcessor, branch_records
from story_engine.wiki.consolidator import GatewayWikiConsolidator
from story_engine.workspace.project_store import ProjectStore

_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class SimulationStartRequest(RuntimeModel):
    branch_id: Identifier = "main"
    premise_text: str = Field(min_length=1, max_length=131_072)
    actor_ids: tuple[Identifier, ...] = ()
    content_locale: LocaleCode = "zh-CN"
    control: ControlPolicy
    output: OutputPolicy = OutputPolicy()
    seed: int | None = None


class InteractiveTurnRequest(RuntimeModel):
    text: str = Field(min_length=1, max_length=32_768)
    command_id: Identifier = Field(
        default_factory=lambda: f"interactive:{uuid.uuid4().hex}"
    )


class SimulationTerminateRequest(RuntimeModel):
    reason_text: str = Field(min_length=1, max_length=16_384)


class SimulationRestoreRequest(RuntimeModel):
    checkpoint_id: Identifier


class SimulationLocaleRequest(RuntimeModel):
    content_locale: LocaleCode


class SimulationAdvanceRequest(RuntimeModel):
    command_id: Identifier
    expected_state_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class ProjectionRebuildRequest(RuntimeModel):
    kind: ProjectionKind | None = None


class CheckpointRequest(RuntimeModel):
    reason: str = Field(default="user checkpoint", min_length=1, max_length=1_024)


class BranchCreateRequest(RuntimeModel):
    branch_id: Identifier
    source_checkpoint_id: Identifier
    parent_branch_id: Identifier = "main"
    content_locale: LocaleCode = "zh-CN"


class BranchRollbackRequest(RuntimeModel):
    checkpoint_id: Identifier


class BranchComparisonResponse(RuntimeModel):
    left: BranchManifest
    right: BranchManifest
    only_left_events: tuple[str, ...]
    only_right_events: tuple[str, ...]
    only_left_entities: tuple[Identifier, ...]
    only_right_entities: tuple[Identifier, ...]


def _require_project(settings: EngineSettings, project_id: str) -> Path:
    if not _PROJECT_ID.fullmatch(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    root = settings.projects_root / project_id
    if not (root / "project.md").is_file():
        raise HTTPException(status_code=404, detail="Project not found")
    return root


def create_simulations_router(
    settings: EngineSettings,
    service: SimulationApplicationService,
    gateway: ModelGateway,
) -> APIRouter:
    router = APIRouter(tags=["simulations"])

    def ensure_default_world(project_id: str) -> Path:
        if project_id != "last-ferry-before":
            return _require_project(settings, project_id)
        root = settings.projects_root / project_id
        if not (root / "project.md").is_file():
            settings.projects_root.mkdir(parents=True, exist_ok=True)
            with suppress(FileExistsError):
                SubmissionService(settings.projects_root).finalize(
                    last_ferry_before_submission()
                )
        return _require_project(settings, project_id)

    def interactive_session(
        project_id: str,
        branch_id: str = "main",
    ) -> TurnSessionSnapshot:
        root = ensure_default_world(project_id)
        kernel = SimulationCommitKernel(root)
        try:
            branch = kernel.branches.load(branch_id)
        except FileNotFoundError as error:
            if branch_id != "main":
                raise HTTPException(
                    status_code=404,
                    detail="Branch not found",
                ) from error
            branch = None
        if branch is not None and branch.head_checkpoint_id is not None:
            if branch.project_id != project_id:
                raise HTTPException(status_code=404, detail="Branch not found")
            checkpoint = kernel.load_checkpoint(project_id, branch.head_checkpoint_id)
            if checkpoint.player_actor_id is None:
                raise HTTPException(
                    status_code=409,
                    detail="Project has no configured human actor",
                )
            restored = service.restore_branch(project_id, branch_id=branch_id)
            return service.resume_pending_interactive_handoff(restored)

        if branch_id != "main":
            raise HTTPException(status_code=404, detail="Branch not found")

        project = ProjectStore(root).load()
        player = next(
            (character for character in project.characters if character.id == "player"),
            None,
        )
        if player is None or player.type != "active":
            raise HTTPException(
                status_code=409,
                detail="Project has no configured human actor",
            )
        return service.start(
            TurnSessionRequest(
                project_id=project_id,
                branch_id=branch_id,
                premise_text=project.world.scene_text or project.project.title,
                actor_ids=tuple(
                    character.id
                    for character in project.characters
                    if character.type == "active"
                ),
                player_actor_id=player.id,
                content_locale="zh-CN",
                control=ControlPolicy(mode=ControlMode.STEP, max_steps=10_000),
                output=OutputPolicy(),
            )
        )

    def require_live_session(
        project_id: str,
        session_id: str,
    ) -> TurnSessionSnapshot:
        _require_project(settings, project_id)
        try:
            state = service.get_durable(project_id, session_id)
        except (FileNotFoundError, SessionNotFoundError) as error:
            raise HTTPException(
                status_code=404,
                detail="Simulation not found",
            ) from error
        if isinstance(state, SessionManifest):
            raise HTTPException(
                status_code=409,
                detail=f"Simulation is {state.status.value} and is read-only",
            )
        if state.project_id != project_id:
            raise HTTPException(status_code=404, detail="Simulation not found")
        return state

    def kernel_for(project_id: str) -> SimulationCommitKernel:
        return SimulationCommitKernel(_require_project(settings, project_id))

    @router.post(
        "/projects/{project_id}/simulations",
        response_model=TurnSessionSnapshot,
        status_code=status.HTTP_201_CREATED,
    )
    async def start_simulation(
        project_id: str,
        request: SimulationStartRequest,
    ) -> TurnSessionSnapshot:
        _require_project(settings, project_id)
        try:
            return service.start(
                TurnSessionRequest(
                    project_id=project_id,
                    branch_id=request.branch_id,
                    premise_text=request.premise_text,
                    actor_ids=request.actor_ids,
                    content_locale=request.content_locale,
                    control=request.control,
                    output=request.output,
                    seed=request.seed,
                )
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (BranchAlreadyActiveError, OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulation/turn",
        response_model=InteractiveTurnResponse,
    )
    async def interactive_turn(
        project_id: str,
        request: InteractiveTurnRequest,
        branch_id: str = "main",
    ) -> InteractiveTurnResponse:
        try:
            session = await asyncio.to_thread(
                interactive_session,
                project_id,
                branch_id,
            )
            result = await asyncio.to_thread(
                service.interactive_turn,
                session.session_id,
                text=request.text,
                command_id=request.command_id,
            )
            snapshot = service.get(session.session_id)
            return PerceptionBuilder().build(
                snapshot,
                result,
                scene_events=service.current_scene_events(snapshot),
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (
            BranchAlreadyActiveError,
            InvalidSessionTransitionError,
            SessionCommandConflictError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/simulation/session",
        response_model=InteractiveTurnResponse,
    )
    async def get_interactive_session(
        project_id: str,
        branch_id: str = "main",
    ) -> InteractiveTurnResponse:
        try:
            snapshot = await asyncio.to_thread(
                interactive_session,
                project_id,
                branch_id,
            )
            return PerceptionBuilder().initial(
                snapshot,
                scene_events=service.current_scene_events(snapshot),
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (
            BranchAlreadyActiveError,
            InvalidSessionTransitionError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/simulations",
        response_model=tuple[SessionManifest, ...],
    )
    async def list_simulations(project_id: str) -> tuple[SessionManifest, ...]:
        _require_project(settings, project_id)
        return service.list(project_id)

    @router.post(
        "/projects/{project_id}/simulations/restore",
        response_model=TurnSessionSnapshot,
    )
    async def restore_simulation(
        project_id: str,
        request: SimulationRestoreRequest,
    ) -> TurnSessionSnapshot:
        _require_project(settings, project_id)
        try:
            return service.restore(
                project_id,
                checkpoint_id=request.checkpoint_id,
            )
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail="Checkpoint not found",
            ) from error
        except (InvalidSessionTransitionError, OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/simulation-events",
        response_model=tuple[EngineEvent, ...],
    )
    async def list_simulation_events(
        project_id: str,
        after_sequence: int = 0,
    ) -> tuple[EngineEvent, ...]:
        _require_project(settings, project_id)
        events = service.event_bus.events_after(
            project_id=project_id,
            after_sequence=max(0, after_sequence),
        )
        if events is None:
            raise HTTPException(
                status_code=409,
                detail="event history is outside the replay window",
            )
        return events

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/simulation-trace",
        response_model=tuple[SimulationLogRecord, ...],
    )
    async def list_simulation_trace(
        project_id: str,
        branch_id: str,
        after_step: int = -1,
    ) -> tuple[SimulationLogRecord, ...]:
        records = branch_records(settings.projects_root / project_id, branch_id)
        return tuple(record for record in records if record.result.step > after_step)

    @router.get(
        "/projects/{project_id}/simulations/{session_id}",
        response_model=TurnSessionSnapshot | SessionManifest,
    )
    async def get_simulation(
        project_id: str,
        session_id: str,
    ) -> TurnSessionSnapshot | SessionManifest:
        _require_project(settings, project_id)
        try:
            return service.get_durable(project_id, session_id)
        except (FileNotFoundError, SessionNotFoundError) as error:
            raise HTTPException(
                status_code=404,
                detail="Simulation not found",
            ) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/step",
        response_model=StepResult,
    )
    async def step_simulation(
        project_id: str,
        session_id: str,
        request: SimulationAdvanceRequest,
    ) -> StepResult:
        require_live_session(project_id, session_id)
        try:
            return await asyncio.to_thread(
                service.step,
                session_id,
                command_id=request.command_id,
                expected_state_hash=request.expected_state_hash,
                cancellation=Event(),
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (
            InvalidSessionTransitionError,
            SessionCommandConflictError,
            RuntimeError,
            ValueError,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/run",
        response_model=TurnSessionSnapshot,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def run_simulation(
        project_id: str,
        session_id: str,
        request: SimulationAdvanceRequest,
    ) -> TurnSessionSnapshot:
        require_live_session(project_id, session_id)
        try:
            return service.run_in_background(
                session_id,
                command_id=request.command_id,
                expected_state_hash=request.expected_state_hash,
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (
            InvalidSessionTransitionError,
            SessionCommandConflictError,
            RuntimeError,
            ValueError,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/pause",
        response_model=TurnSessionSnapshot,
    )
    async def pause_simulation(
        project_id: str,
        session_id: str,
    ) -> TurnSessionSnapshot:
        require_live_session(project_id, session_id)
        try:
            return service.pause(session_id)
        except InvalidSessionTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/resume",
        response_model=TurnSessionSnapshot,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def resume_simulation(
        project_id: str,
        session_id: str,
        request: SimulationAdvanceRequest,
    ) -> TurnSessionSnapshot:
        require_live_session(project_id, session_id)
        try:
            return service.run_in_background(
                session_id,
                command_id=request.command_id,
                expected_state_hash=request.expected_state_hash,
                resume=True,
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (
            InvalidSessionTransitionError,
            SessionCommandConflictError,
            RuntimeError,
            ValueError,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/simulations/{session_id}/projections",
        response_model=tuple[ProjectionTask, ...],
    )
    async def list_projection_tasks(
        project_id: str,
        session_id: str,
    ) -> tuple[ProjectionTask, ...]:
        _require_project(settings, project_id)
        try:
            service.get_durable(project_id, session_id)
            return service.list_projections(
                project_id=project_id,
                session_id=session_id,
            )
        except (FileNotFoundError, SessionNotFoundError) as error:
            raise HTTPException(
                status_code=404,
                detail="Simulation not found",
            ) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/projections/{task_id}/retry",
        response_model=ProjectionTask,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def retry_projection_task(
        project_id: str,
        session_id: str,
        task_id: str,
    ) -> ProjectionTask:
        _require_project(settings, project_id)
        try:
            service.get_durable(project_id, session_id)
            return service.retry_projection(
                project_id=project_id,
                session_id=session_id,
                task_id=task_id,
            )
        except (FileNotFoundError, SessionNotFoundError) as error:
            raise HTTPException(
                status_code=404,
                detail="Projection task not found",
            ) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/projections/rebuild",
        response_model=tuple[ProjectionTask, ...],
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def rebuild_projection_tasks(
        project_id: str,
        session_id: str,
        request: ProjectionRebuildRequest,
    ) -> tuple[ProjectionTask, ...]:
        _require_project(settings, project_id)
        try:
            return service.rebuild_projections(
                project_id=project_id,
                session_id=session_id,
                kind=request.kind,
            )
        except (FileNotFoundError, SessionNotFoundError) as error:
            raise HTTPException(
                status_code=404,
                detail="Simulation not found",
            ) from error
        except (RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/terminate",
        response_model=TurnSessionSnapshot,
    )
    async def terminate_simulation(
        project_id: str,
        session_id: str,
        request: SimulationTerminateRequest,
    ) -> TurnSessionSnapshot:
        require_live_session(project_id, session_id)
        try:
            return service.terminate(session_id, reason_text=request.reason_text)
        except InvalidSessionTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/cancel",
        response_model=TurnSessionSnapshot,
    )
    async def cancel_simulation(
        project_id: str,
        session_id: str,
        request: SimulationTerminateRequest,
    ) -> TurnSessionSnapshot:
        require_live_session(project_id, session_id)
        try:
            return service.cancel(session_id, reason_text=request.reason_text)
        except InvalidSessionTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/locale",
        response_model=TurnSessionSnapshot,
    )
    async def switch_simulation_locale(
        project_id: str,
        session_id: str,
        request: SimulationLocaleRequest,
    ) -> TurnSessionSnapshot:
        require_live_session(project_id, session_id)
        try:
            return service.switch_locale(
                session_id,
                content_locale=request.content_locale,
            )
        except (InvalidSessionTransitionError, OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/checkpoint",
        response_model=CommitResult,
    )
    async def checkpoint_simulation(
        project_id: str,
        session_id: str,
        request: CheckpointRequest,
    ) -> CommitResult:
        require_live_session(project_id, session_id)
        try:
            return service.checkpoint(session_id, reason=request.reason)
        except (
            InvalidSessionTransitionError,
            OSError,
            RuntimeError,
            ValueError,
        ) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/branches",
        response_model=BranchManifest,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_branch(
        project_id: str,
        request: BranchCreateRequest,
    ) -> BranchManifest:
        try:
            return kernel_for(project_id).create_branch(
                project_id,
                source_checkpoint_id=request.source_checkpoint_id,
                branch_id=request.branch_id,
                parent_branch_id=request.parent_branch_id,
                content_locale=request.content_locale,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Branch not found") from error
        except (FileExistsError, OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/branches",
        response_model=tuple[BranchManifest, ...],
    )
    async def list_branches(project_id: str) -> tuple[BranchManifest, ...]:
        branches = kernel_for(project_id).branches.list()
        return tuple(branch for branch in branches if branch.project_id == project_id)

    @router.get(
        "/projects/{project_id}/branches/compare",
        response_model=BranchComparisonResponse,
    )
    async def compare_branches(
        project_id: str,
        left: str,
        right: str,
    ) -> BranchComparisonResponse:
        kernel = kernel_for(project_id)
        try:
            left_branch = kernel.branches.load(left)
            right_branch = kernel.branches.load(right)
            if (
                left_branch.project_id != project_id
                or right_branch.project_id != project_id
            ):
                raise FileNotFoundError
            left_records = (
                kernel.logs.reachable(
                    kernel.checkpoints,
                    left_branch.head_checkpoint_id,
                    branch_id=left,
                )
                if left_branch.head_checkpoint_id is not None
                else ()
            )
            right_records = (
                kernel.logs.reachable(
                    kernel.checkpoints,
                    right_branch.head_checkpoint_id,
                    branch_id=right,
                )
                if right_branch.head_checkpoint_id is not None
                else ()
            )

            def event_texts(
                records: tuple[SimulationLogRecord, ...],
            ) -> tuple[str, ...]:
                return tuple(
                    record.result.resolved_turn.raw_resolution_text
                    for record in records
                    if record.result.resolved_turn is not None
                )

            left_events = event_texts(left_records)
            right_events = event_texts(right_records)
            common = 0
            for left_event, right_event in zip(left_events, right_events, strict=False):
                if left_event != right_event:
                    break
                common += 1
            left_snapshot = (
                kernel.load_checkpoint(project_id, left_branch.head_checkpoint_id)
                if left_branch.head_checkpoint_id
                else None
            )
            right_snapshot = (
                kernel.load_checkpoint(project_id, right_branch.head_checkpoint_id)
                if right_branch.head_checkpoint_id
                else None
            )
            left_entities = set(left_snapshot.roster_actor_ids if left_snapshot else ())
            right_entities = set(
                right_snapshot.roster_actor_ids if right_snapshot else ()
            )
            return BranchComparisonResponse(
                left=left_branch,
                right=right_branch,
                only_left_events=left_events[common:],
                only_right_events=right_events[common:],
                only_left_entities=tuple(sorted(left_entities - right_entities)),
                only_right_entities=tuple(sorted(right_entities - left_entities)),
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Branch not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/timeline",
        response_model=tuple[CheckpointTimelineEntry, ...],
    )
    async def branch_timeline(
        project_id: str,
        branch_id: str,
    ) -> tuple[CheckpointTimelineEntry, ...]:
        try:
            kernel = kernel_for(project_id)
            branch = kernel.branches.load(branch_id)
            if branch.project_id != project_id:
                raise FileNotFoundError(branch_id)
            if branch.head_checkpoint_id is None:
                return ()
            entries = []
            for checkpoint_id in kernel.checkpoints.lineage(branch.head_checkpoint_id):
                snapshot = kernel.load_checkpoint(project_id, checkpoint_id)
                if snapshot.world is None:
                    raise ValueError("checkpoint has no player-visible world state")
                entries.append(
                    CheckpointTimelineEntry(
                        checkpoint_id=checkpoint_id,
                        step=snapshot.current_step,
                        world_time=snapshot.world.current_time,
                        is_current=checkpoint_id == branch.head_checkpoint_id,
                    )
                )
            return tuple(entries)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Branch not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/branches/{branch_id}",
        response_model=BranchManifest,
    )
    async def get_branch(project_id: str, branch_id: str) -> BranchManifest:
        try:
            branch = kernel_for(project_id).branches.load(branch_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Branch not found") from error
        if branch.project_id != project_id:
            raise HTTPException(status_code=404, detail="Branch not found")
        return branch

    @router.post(
        "/projects/{project_id}/branches/{branch_id}/rollback",
        response_model=BranchManifest,
    )
    async def rollback_branch(
        project_id: str,
        branch_id: str,
        request: BranchRollbackRequest,
    ) -> BranchManifest:
        try:
            kernel = kernel_for(project_id)
            branch = kernel.rollback_branch(
                project_id,
                branch_id,
                checkpoint_id=request.checkpoint_id,
            )
            snapshot = kernel.load_checkpoint(project_id, request.checkpoint_id)
            branch_snapshot = snapshot.model_copy(
                update={
                    "branch_id": branch_id,
                    "checkpoint_id": request.checkpoint_id,
                    "request": snapshot.request.model_copy(
                        update={"branch_id": branch_id}
                    ),
                }
            )
            await WikiBoundaryProcessor(
                settings.projects_root / project_id,
                consolidator=GatewayWikiConsolidator(gateway),
            ).rebuild(branch_snapshot)
            return branch
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Branch not found") from error
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
