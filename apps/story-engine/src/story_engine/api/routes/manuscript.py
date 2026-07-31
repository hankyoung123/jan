import re

from fastapi import APIRouter, HTTPException, status

from story_engine.api.model_errors import model_http_error
from story_engine.api.routes.projects import _require_project
from story_engine.config import EngineSettings
from story_engine.domain.errors import DomainError
from story_engine.manuscript.models import (
    AmendmentCommitResult,
    ManuscriptExport,
    SceneDraft,
    SceneGenerationRequest,
    SceneMutationResult,
    SceneUpdateRequest,
)
from story_engine.manuscript.service import (
    GatewayManuscriptAgent,
    ManuscriptService,
)
from story_engine.models.errors import ModelGatewayError
from story_engine.models.gateway import ModelGateway

_SCENE_ID = re.compile(r"^scene-[0-9]{6}$")
_AMENDMENT_ID = re.compile(r"^amendment-scene-[0-9]{6}-[0-9]{6}$")


def _require_identifier(value: str, pattern: re.Pattern[str], label: str) -> None:
    if not pattern.fullmatch(value):
        raise HTTPException(status_code=404, detail=f"{label} not found")


def create_manuscript_router(
    settings: EngineSettings,
    model_gateway: ModelGateway,
) -> APIRouter:
    router = APIRouter(tags=["manuscript"])

    def service(project_id: str) -> ManuscriptService:
        return ManuscriptService(
            _require_project(settings, project_id),
            agent=GatewayManuscriptAgent(model_gateway),
        )

    @router.get(
        "/projects/{project_id}/scenes",
        response_model=tuple[SceneDraft, ...],
    )
    async def list_scenes(project_id: str) -> tuple[SceneDraft, ...]:
        return service(project_id).list_scenes()

    @router.get(
        "/projects/{project_id}/scenes/{scene_id}",
        response_model=SceneDraft,
    )
    async def get_scene(project_id: str, scene_id: str) -> SceneDraft:
        _require_identifier(scene_id, _SCENE_ID, "Scene")
        try:
            return service(project_id).get_scene(scene_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Scene not found") from error

    @router.post(
        "/projects/{project_id}/scenes/generate",
        response_model=SceneDraft,
        status_code=status.HTTP_201_CREATED,
    )
    async def generate_scene(
        project_id: str,
        request: SceneGenerationRequest,
    ) -> SceneDraft:
        try:
            return await service(project_id).generate_scene(
                request.event_ids,
                chapter_id=request.chapter_id,
            )
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (DomainError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.put(
        "/projects/{project_id}/scenes/{scene_id}",
        response_model=SceneMutationResult,
    )
    async def update_scene(
        project_id: str,
        scene_id: str,
        request: SceneUpdateRequest,
    ) -> SceneMutationResult:
        _require_identifier(scene_id, _SCENE_ID, "Scene")
        try:
            return await service(project_id).update_scene(scene_id, request)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Scene not found") from error
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (DomainError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/scenes/{scene_id}/amendments/"
        "{amendment_id}/confirm",
        response_model=AmendmentCommitResult,
    )
    async def confirm_amendment(
        project_id: str,
        scene_id: str,
        amendment_id: str,
    ) -> AmendmentCommitResult:
        _require_identifier(scene_id, _SCENE_ID, "Scene")
        _require_identifier(amendment_id, _AMENDMENT_ID, "Amendment")
        try:
            return service(project_id).confirm_amendment(
                amendment_id,
                expected_scene_id=scene_id,
            )
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail="Amendment not found",
            ) from error
        except (DomainError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/manuscript/export",
        response_model=ManuscriptExport,
    )
    async def export_manuscript(project_id: str) -> ManuscriptExport:
        return service(project_id).export_markdown()

    return router
