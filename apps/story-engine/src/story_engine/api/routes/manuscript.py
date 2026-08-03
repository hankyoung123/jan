import re

from fastapi import APIRouter, HTTPException, status

from story_engine.api.model_errors import model_http_error
from story_engine.api.routes.projects import _require_project
from story_engine.config import EngineSettings
from story_engine.domain.narrative import NarrativeSourceSummary
from story_engine.manuscript.models import (
    ManuscriptExport,
    SceneDraft,
    SceneGenerationRequest,
    SceneMutationResult,
    SceneUpdateRequest,
)
from story_engine.manuscript.service import (
    GatewayManuscriptAgent,
    ManuscriptService,
    VersionConflictError,
)
from story_engine.models.errors import ModelGatewayError
from story_engine.models.gateway import ModelGateway
from story_engine.models.policy import ProjectModelPolicyStore
from story_engine.workspace.session import WorkspaceSessionManager

_SCENE_ID = re.compile(r"^scene-[0-9]{6}$")


def _require_scene_id(scene_id: str) -> None:
    if not _SCENE_ID.fullmatch(scene_id):
        raise HTTPException(status_code=404, detail="Scene not found")


def create_manuscript_router(
    settings: EngineSettings,
    model_gateway: ModelGateway,
    workspace_manager: WorkspaceSessionManager,
) -> APIRouter:
    router = APIRouter(tags=["manuscript"])

    def service(project_id: str, branch_id: str) -> ManuscriptService:
        workspace_manager.open(project_id)
        try:
            root = _require_project(settings, project_id)
            policy = ProjectModelPolicyStore(
                root,
                model_gateway.registry,
            ).load()
            return ManuscriptService(
                root,
                branch_id,
                agent=GatewayManuscriptAgent(
                    model_gateway,
                    writer_profile_id=policy.task_profile_ids["writer"],
                    editor_profile_id=policy.task_profile_ids["editor"],
                ),
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Branch not found") from error
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/narrative-sources",
        response_model=tuple[NarrativeSourceSummary, ...],
    )
    async def list_narrative_sources(
        project_id: str,
        branch_id: str,
        after_step: int | None = None,
    ) -> tuple[NarrativeSourceSummary, ...]:
        try:
            return service(project_id, branch_id).list_sources(
                after_step=after_step,
            )
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/manuscript/scenes",
        response_model=tuple[SceneDraft, ...],
    )
    async def list_scenes(
        project_id: str,
        branch_id: str,
    ) -> tuple[SceneDraft, ...]:
        return service(project_id, branch_id).list_scenes()

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/manuscript/scenes/{scene_id}",
        response_model=SceneDraft,
    )
    async def get_scene(
        project_id: str,
        branch_id: str,
        scene_id: str,
    ) -> SceneDraft:
        _require_scene_id(scene_id)
        try:
            return service(project_id, branch_id).get_scene(scene_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Scene not found") from error

    @router.post(
        "/projects/{project_id}/branches/{branch_id}/manuscript/scenes/generate",
        response_model=SceneDraft,
        status_code=status.HTTP_201_CREATED,
    )
    async def generate_scene(
        project_id: str,
        branch_id: str,
        request: SceneGenerationRequest,
    ) -> SceneDraft:
        try:
            return await service(project_id, branch_id).generate_scene(
                checkpoint_id=request.checkpoint_id,
                from_step=request.from_step,
                to_step=request.to_step,
                chapter_id=request.chapter_id,
                viewpoint_actor_id=request.viewpoint_actor_id,
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.put(
        "/projects/{project_id}/branches/{branch_id}/manuscript/scenes/{scene_id}",
        response_model=SceneMutationResult,
    )
    async def update_scene(
        project_id: str,
        branch_id: str,
        scene_id: str,
        request: SceneUpdateRequest,
    ) -> SceneMutationResult:
        _require_scene_id(scene_id)
        try:
            return await service(project_id, branch_id).update_scene(
                scene_id,
                request,
            )
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Scene not found") from error
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (OSError, ValueError, VersionConflictError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/manuscript/export",
        response_model=ManuscriptExport,
    )
    async def export_manuscript(
        project_id: str,
        branch_id: str,
    ) -> ManuscriptExport:
        return service(project_id, branch_id).export_markdown()

    return router
