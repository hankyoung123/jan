import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from story_engine.api.routes.projects import _require_project
from story_engine.config import EngineSettings
from story_engine.domain.base import Identifier, RuntimeModel
from story_engine.domain.world_bible import (
    DirectorInstruction,
    WorldBibleSnapshot,
)
from story_engine.projection.world_bible import WorldBibleService, WorldBibleStore


class WorldBibleRebuildRequest(RuntimeModel):
    checkpoint_id: Identifier | None = None


class DirectorInstructionRequest(RuntimeModel):
    checkpoint_id: Identifier
    text: str = Field(min_length=1, max_length=65_536)


def create_world_bible_router(settings: EngineSettings) -> APIRouter:
    router = APIRouter(tags=["world-bible"])

    def service(project_id: str, branch_id: str) -> WorldBibleService:
        return WorldBibleService(_require_project(settings, project_id), branch_id)

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/world-bible",
        response_model=WorldBibleSnapshot,
    )
    async def get_world_bible(
        project_id: str,
        branch_id: str,
        checkpoint_id: str | None = None,
    ) -> WorldBibleSnapshot:
        try:
            return service(project_id, branch_id).get(checkpoint_id)
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail="World Bible not found",
            ) from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/branches/{branch_id}/world-bible/rebuild",
        response_model=WorldBibleSnapshot,
    )
    async def rebuild_world_bible(
        project_id: str,
        branch_id: str,
        request: WorldBibleRebuildRequest,
    ) -> WorldBibleSnapshot:
        try:
            return service(project_id, branch_id).rebuild(request.checkpoint_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Branch not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/director-instructions",
        response_model=tuple[DirectorInstruction, ...],
    )
    async def list_director_instructions(
        project_id: str,
        branch_id: str,
    ) -> tuple[DirectorInstruction, ...]:
        root = _require_project(settings, project_id)
        return WorldBibleStore(root, branch_id).list_instructions()

    @router.post(
        "/projects/{project_id}/branches/{branch_id}/director-instructions",
        response_model=DirectorInstruction,
        status_code=status.HTTP_201_CREATED,
    )
    async def add_director_instruction(
        project_id: str,
        branch_id: str,
        request: DirectorInstructionRequest,
    ) -> DirectorInstruction:
        root = _require_project(settings, project_id)
        instruction = DirectorInstruction(
            instruction_id=f"director:{uuid.uuid4().hex}",
            text=request.text,
            created_at=datetime.now(UTC),
            applies_from_checkpoint_id=request.checkpoint_id,
        )
        try:
            WorldBibleStore(root, branch_id).add_instruction(instruction)
            return instruction
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    return router
