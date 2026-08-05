import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, status

from story_engine.api.model_errors import model_http_error
from story_engine.config import EngineSettings
from story_engine.domain.errors import DomainError
from story_engine.models.errors import ModelGatewayError
from story_engine.models.gateway import ModelGateway
from story_engine.submission.service import (
    SubmissionConversationRequest,
    SubmissionConversationResponse,
    SubmissionConversationUpdate,
    SubmissionDiscussionService,
    SubmissionPackage,
    SubmissionService,
    SubmissionStatus,
    SubmissionWorkspaceState,
    SubmissionWorkspaceStore,
)
from story_engine.workspace.project_store import ProjectSnapshot
from story_engine.workspace.session import (
    ProjectCatalogEntry,
    WorkspaceClosedState,
    WorkspaceNotOpenError,
    WorkspaceSessionManager,
    WorkspaceState,
)

_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def _project_root(settings: EngineSettings, project_id: str) -> Path:
    if not _PROJECT_ID.fullmatch(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return settings.projects_root / project_id


def _require_project(settings: EngineSettings, project_id: str) -> Path:
    root = _project_root(settings, project_id)
    if not (root / "project.md").is_file():
        raise HTTPException(status_code=404, detail="Project not found")
    return root


def create_projects_router(
    settings: EngineSettings,
    model_gateway: ModelGateway,
    workspace_manager: WorkspaceSessionManager,
) -> APIRouter:
    router = APIRouter(tags=["projects"])

    @router.get("/projects", response_model=tuple[ProjectCatalogEntry, ...])
    async def list_projects() -> tuple[ProjectCatalogEntry, ...]:
        return workspace_manager.list_projects()

    @router.post("/projects/{project_id}/open", response_model=WorkspaceState)
    async def open_project(project_id: str) -> WorkspaceState:
        _require_project(settings, project_id)
        try:
            return workspace_manager.open(project_id)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/close",
        response_model=WorkspaceClosedState,
    )
    async def close_project(project_id: str) -> WorkspaceClosedState:
        _require_project(settings, project_id)
        return workspace_manager.close(project_id)

    @router.get(
        "/projects/{project_id}/workspace",
        response_model=WorkspaceState,
    )
    async def get_workspace(project_id: str) -> WorkspaceState:
        _require_project(settings, project_id)
        try:
            return workspace_manager.get(project_id)
        except WorkspaceNotOpenError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/submission/messages",
        response_model=SubmissionConversationResponse,
    )
    async def discuss_submission(
        project_id: str,
        request: SubmissionConversationRequest,
    ) -> SubmissionConversationResponse:
        root = _project_root(settings, project_id)
        if request.draft.id != project_id:
            raise HTTPException(
                status_code=409,
                detail="Submission draft does not match project id",
            )
        if (root / "project.md").is_file():
            raise HTTPException(status_code=409, detail="Project already exists")
        try:
            response = await SubmissionDiscussionService(model_gateway).respond(request)
            SubmissionWorkspaceStore(root).save(
                SubmissionWorkspaceState(
                    draft=response.draft,
                    messages=(*request.messages, response.message),
                    status=SubmissionStatus(
                        runnable=response.runnable,
                        missing_requirements=response.missing_requirements,
                        review=response.review,
                        updated_at=response.message.metadata.createdAt,
                    ),
                )
            )
            return response
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (DomainError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/submission",
        response_model=SubmissionWorkspaceState,
    )
    async def get_submission(project_id: str) -> SubmissionWorkspaceState:
        store = SubmissionWorkspaceStore(_project_root(settings, project_id))
        if not store.exists():
            raise HTTPException(status_code=404, detail="Submission not found")
        try:
            return store.load()
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.put(
        "/projects/{project_id}/submission/messages",
        response_model=SubmissionWorkspaceState,
    )
    async def update_submission_messages(
        project_id: str,
        request: SubmissionConversationUpdate,
    ) -> SubmissionWorkspaceState:
        store = SubmissionWorkspaceStore(_project_root(settings, project_id))
        if not store.exists():
            raise HTTPException(status_code=404, detail="Submission not found")
        try:
            state = store.load().model_copy(update={"messages": request.messages})
            store.save(state)
            return state
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/submissions/finalize",
        response_model=ProjectSnapshot,
        status_code=status.HTTP_201_CREATED,
    )
    async def finalize_submission(package: SubmissionPackage) -> ProjectSnapshot:
        settings.projects_root.mkdir(parents=True, exist_ok=True)
        try:
            snapshot = SubmissionService(settings.projects_root).finalize(package)
            submission_store = SubmissionWorkspaceStore(
                settings.projects_root / package.id
            )
            if submission_store.exists():
                submission_store.mark_finalized()
            return workspace_manager.open(snapshot.project.id).project
        except FileExistsError as error:
            raise HTTPException(
                status_code=409,
                detail="Project already exists",
            ) from error
        except DomainError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/projects/{project_id}", response_model=ProjectSnapshot)
    async def get_project(project_id: str) -> ProjectSnapshot:
        _require_project(settings, project_id)
        try:
            return workspace_manager.open(project_id).project
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
