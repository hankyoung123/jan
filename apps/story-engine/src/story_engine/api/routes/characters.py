from pathlib import Path

from fastapi import APIRouter, HTTPException

from story_engine.api.routes.projects import _require_project
from story_engine.config import EngineSettings
from story_engine.domain.models import Character
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.workspace.project_store import ProjectStore


def _characters(root: Path, branch_id: str) -> tuple[Character, ...]:
    try:
        branch = BranchStore(root).load(branch_id)
    except FileNotFoundError:
        return ProjectStore(root).load().characters
    if branch.head_checkpoint_id is None:
        return ProjectStore(root).load().characters
    return CheckpointStore(root).load(branch.head_checkpoint_id).characters


def _require_characters(root: Path, branch_id: str) -> tuple[Character, ...]:
    try:
        return _characters(root, branch_id)
    except (OSError, ValueError) as error:
        raise HTTPException(
            status_code=409,
            detail="Project character state is incompatible with this version",
        ) from error


def create_characters_router(settings: EngineSettings) -> APIRouter:
    router = APIRouter(tags=["characters"])

    @router.get(
        "/projects/{project_id}/characters",
        response_model=tuple[Character, ...],
    )
    async def list_characters(
        project_id: str,
        branch_id: str = "main",
    ) -> tuple[Character, ...]:
        root = _require_project(settings, project_id)
        return _require_characters(root, branch_id)

    @router.get(
        "/projects/{project_id}/characters/{character_id}",
        response_model=Character,
    )
    async def get_character(
        project_id: str,
        character_id: str,
        branch_id: str = "main",
    ) -> Character:
        root = _require_project(settings, project_id)
        for character in _require_characters(root, branch_id):
            if character.id == character_id:
                return character
        raise HTTPException(status_code=404, detail="Character not found")

    return router
