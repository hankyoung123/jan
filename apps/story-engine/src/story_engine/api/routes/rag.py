from fastapi import APIRouter, HTTPException

from story_engine.api.routes.projects import _require_project
from story_engine.config import EngineSettings
from story_engine.rag.models import (
    RagIndexSummary,
    RagSearchRequest,
    RagSearchResult,
)
from story_engine.rag.service import RagService
from story_engine.workspace.session import WorkspaceSessionManager


def create_rag_router(
    settings: EngineSettings,
    workspace_manager: WorkspaceSessionManager,
) -> APIRouter:
    router = APIRouter(tags=["rag"])

    def service(project_id: str) -> RagService:
        root = _require_project(settings, project_id)
        workspace_manager.open(project_id)
        return RagService(root)

    @router.post(
        "/projects/{project_id}/rag/rebuild",
        response_model=RagIndexSummary,
    )
    async def rebuild_index(project_id: str) -> RagIndexSummary:
        try:
            return RagIndexSummary.from_index(service(project_id).rebuild_index())
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/rag/search",
        response_model=RagSearchResult,
    )
    async def search(
        project_id: str,
        request: RagSearchRequest,
    ) -> RagSearchResult:
        try:
            return service(project_id).search(request)
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
