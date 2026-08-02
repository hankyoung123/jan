import asyncio
import re
from pathlib import Path
from threading import Event

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from story_engine.api.model_errors import model_http_error
from story_engine.config import EngineSettings
from story_engine.domain.base import Identifier, LocaleCode, RuntimeModel
from story_engine.domain.simulation import (
    BranchManifest,
    CommitResult,
    ControlPolicy,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
)
from story_engine.models.errors import ModelGatewayError
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.simulation.engine import (
    InvalidSessionTransitionError,
    SessionNotFoundError,
)
from story_engine.simulation.execution import BranchAlreadyActiveError
from story_engine.simulation.service import SimulationApplicationService

_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class SimulationStartRequest(RuntimeModel):
    branch_id: Identifier = "main"
    premise_text: str = Field(min_length=1, max_length=131_072)
    actor_ids: tuple[Identifier, ...] = ()
    content_locale: LocaleCode = "zh-CN"
    control: ControlPolicy
    seed: int | None = None


class SimulationTerminateRequest(RuntimeModel):
    reason_text: str = Field(min_length=1, max_length=16_384)


class SimulationLocaleRequest(RuntimeModel):
    content_locale: LocaleCode


class CheckpointRequest(RuntimeModel):
    reason: str = Field(default="user checkpoint", min_length=1, max_length=1_024)


class BranchCreateRequest(RuntimeModel):
    branch_id: Identifier
    source_checkpoint_id: Identifier
    parent_branch_id: Identifier = "main"
    content_locale: LocaleCode = "zh-CN"


class BranchRollbackRequest(RuntimeModel):
    checkpoint_id: Identifier


class ProjectionRequest(RuntimeModel):
    checkpoint_id: Identifier | None = None


class ProjectionResponse(RuntimeModel):
    branch_id: Identifier
    checkpoint_id: Identifier
    written_paths: tuple[str, ...]


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
) -> APIRouter:
    router = APIRouter(tags=["simulations"])

    def require_matching_session(
        project_id: str,
        session_id: str,
    ) -> TurnSessionSnapshot:
        _require_project(settings, project_id)
        try:
            snapshot = service.get(session_id)
        except SessionNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail="Simulation not found",
            ) from error
        if snapshot.project_id != project_id:
            raise HTTPException(status_code=404, detail="Simulation not found")
        return snapshot

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
                    seed=request.seed,
                )
            )
        except (BranchAlreadyActiveError, OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/simulations/{session_id}",
        response_model=TurnSessionSnapshot,
    )
    async def get_simulation(
        project_id: str,
        session_id: str,
    ) -> TurnSessionSnapshot:
        return require_matching_session(project_id, session_id)

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/step",
        response_model=StepResult,
    )
    async def step_simulation(project_id: str, session_id: str) -> StepResult:
        require_matching_session(project_id, session_id)
        try:
            return await asyncio.to_thread(
                service.step,
                session_id,
                cancellation=Event(),
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (InvalidSessionTransitionError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/run",
        response_model=TurnSessionSnapshot,
    )
    async def run_simulation(
        project_id: str,
        session_id: str,
    ) -> TurnSessionSnapshot:
        require_matching_session(project_id, session_id)
        try:
            return await asyncio.to_thread(
                service.run,
                session_id,
                cancellation=Event(),
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (InvalidSessionTransitionError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/pause",
        response_model=TurnSessionSnapshot,
    )
    async def pause_simulation(
        project_id: str,
        session_id: str,
    ) -> TurnSessionSnapshot:
        require_matching_session(project_id, session_id)
        try:
            return service.pause(session_id)
        except InvalidSessionTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/simulations/{session_id}/resume",
        response_model=TurnSessionSnapshot,
    )
    async def resume_simulation(
        project_id: str,
        session_id: str,
    ) -> TurnSessionSnapshot:
        require_matching_session(project_id, session_id)
        try:
            return await asyncio.to_thread(
                service.resume,
                session_id,
                cancellation=Event(),
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (InvalidSessionTransitionError, ValueError) as error:
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
        require_matching_session(project_id, session_id)
        try:
            return service.terminate(session_id, reason_text=request.reason_text)
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
        require_matching_session(project_id, session_id)
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
        require_matching_session(project_id, session_id)
        try:
            return service.checkpoint(session_id, reason=request.reason)
        except (OSError, ValueError) as error:
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
            return kernel_for(project_id).rollback_branch(
                project_id,
                branch_id,
                checkpoint_id=request.checkpoint_id,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Branch not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/branches/{branch_id}/projection",
        response_model=ProjectionResponse,
    )
    async def rebuild_projection(
        project_id: str,
        branch_id: str,
        request: ProjectionRequest,
    ) -> ProjectionResponse:
        kernel = kernel_for(project_id)
        try:
            branch = kernel.branches.load(branch_id)
            selected = request.checkpoint_id or branch.head_checkpoint_id
            if selected is None:
                raise ValueError("branch has no checkpoint")
            paths = kernel.project_markdown(
                project_id,
                branch_id,
                checkpoint_id=selected,
            )
            return ProjectionResponse(
                branch_id=branch_id,
                checkpoint_id=selected,
                written_paths=tuple(str(path) for path in paths),
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Branch not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
