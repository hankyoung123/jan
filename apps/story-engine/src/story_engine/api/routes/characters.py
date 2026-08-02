import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import Field

from story_engine.api.model_errors import model_http_error
from story_engine.api.routes.projects import _require_project
from story_engine.config import EngineSettings
from story_engine.domain.errors import DomainError, InvalidTransitionError
from story_engine.domain.models import Character, DomainModel
from story_engine.models.errors import ModelGatewayError
from story_engine.models.gateway import ModelGateway
from story_engine.promotion.service import (
    CharacterPromotionService,
    PromotionCommitResult,
)
from story_engine.review.promotion import EditorPromotionReviewer, PromotionAssessment
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.session import WorkspaceSessionManager


class PromotionConfirmationRequest(DomainModel):
    candidate_id: str = Field(pattern=r"^promotion-[a-z0-9][a-z0-9-]*-v[0-9]+$")


def _promotion_service(
    root: Path,
    model_gateway: ModelGateway,
) -> CharacterPromotionService:
    return CharacterPromotionService(
        root,
        reviewer=EditorPromotionReviewer(model_gateway),
    )


def create_characters_router(
    settings: EngineSettings,
    model_gateway: ModelGateway,
    workspace_manager: WorkspaceSessionManager,
) -> APIRouter:
    router = APIRouter(tags=["characters"])

    @router.get(
        "/projects/{project_id}/characters",
        response_model=tuple[Character, ...],
    )
    async def list_characters(project_id: str) -> tuple[Character, ...]:
        root = _require_project(settings, project_id)
        return ProjectStore(root).load().characters

    @router.get(
        "/projects/{project_id}/characters/{character_id}",
        response_model=Character,
    )
    async def get_character(project_id: str, character_id: str) -> Character:
        root = _require_project(settings, project_id)
        for character in ProjectStore(root).load().characters:
            if character.id == character_id:
                return character
        raise HTTPException(status_code=404, detail="Character not found")

    @router.post(
        "/projects/{project_id}/characters/{character_id}/promotion-review",
        response_model=PromotionAssessment,
    )
    async def review_promotion(
        project_id: str,
        character_id: str,
        branch_id: str,
    ) -> PromotionAssessment:
        root = _require_project(settings, project_id)
        try:
            return await asyncio.to_thread(
                _promotion_service(root, model_gateway).review,
                character_id,
                branch_id,
            )
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404, detail="Character not found"
            ) from error
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (DomainError, InvalidTransitionError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/characters/{character_id}/promote",
        response_model=PromotionCommitResult,
    )
    async def confirm_promotion(
        project_id: str,
        character_id: str,
        request: PromotionConfirmationRequest,
    ) -> PromotionCommitResult:
        root = _require_project(settings, project_id)
        try:
            result = await asyncio.to_thread(
                _promotion_service(root, model_gateway).confirm,
                character_id,
                request.candidate_id,
            )
            workspace_manager.refresh(project_id)
            return result
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail="Promotion candidate not found",
            ) from error
        except (DomainError, InvalidTransitionError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
