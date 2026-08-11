import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import Field

from story_engine.api.routes.projects import _require_project
from story_engine.config import EngineSettings
from story_engine.domain.base import Identifier, RuntimeModel
from story_engine.domain.wiki import (
    DirectorInstruction,
    WikiBranchView,
    WikiLintResult,
    WikiPage,
)
from story_engine.models.gateway import ModelGateway
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.wiki.boundary import WikiBoundaryProcessor
from story_engine.wiki.consolidator import GatewayWikiConsolidator
from story_engine.wiki.lint import WikiLinter
from story_engine.wiki.store import WikiRevisionConflictError, WikiStore


class WikiRebuildRequest(RuntimeModel):
    checkpoint_id: Identifier | None = None


class WikiPageUpdateRequest(RuntimeModel):
    path: str = Field(min_length=1, max_length=512)
    content: str = Field(max_length=65_536)
    expected_revision: int = Field(ge=0)


class DirectorInstructionRequest(RuntimeModel):
    checkpoint_id: Identifier
    text: str = Field(min_length=1, max_length=65_536)


def create_wiki_router(settings: EngineSettings, gateway: ModelGateway) -> APIRouter:
    router = APIRouter(tags=["wiki"])

    def root_for(project_id: str):  # type: ignore[no-untyped-def]
        return _require_project(settings, project_id)

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/wiki",
        response_model=WikiBranchView,
    )
    async def get_wiki(project_id: str, branch_id: str) -> WikiBranchView:
        try:
            return WikiStore(root_for(project_id), branch_id).view()
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Wiki not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/wiki/page",
        response_model=WikiPage,
    )
    async def get_wiki_page(
        project_id: str,
        branch_id: str,
        path: str = Query(min_length=1, max_length=512),
    ) -> WikiPage:
        try:
            return WikiStore(root_for(project_id), branch_id).load_page(path)
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail="Wiki page not found",
            ) from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.put(
        "/projects/{project_id}/branches/{branch_id}/wiki/page",
        response_model=WikiPage,
    )
    async def update_wiki_page(
        project_id: str,
        branch_id: str,
        request: WikiPageUpdateRequest,
    ) -> WikiPage:
        try:
            return WikiStore(
                root_for(project_id),
                branch_id,
            ).save_page(
                request.path,
                request.content,
                request.expected_revision,
            )
        except WikiRevisionConflictError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except FileNotFoundError as error:
            raise HTTPException(
                status_code=404,
                detail="Wiki page not found",
            ) from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/branches/{branch_id}/wiki/rebuild",
        response_model=WikiBranchView,
    )
    async def rebuild_wiki(
        project_id: str,
        branch_id: str,
        request: WikiRebuildRequest,
    ) -> WikiBranchView:
        root = root_for(project_id)
        try:
            branch = BranchStore(root).load(branch_id)
            selected = request.checkpoint_id or branch.head_checkpoint_id
            if selected is None:
                raise ValueError("branch has no checkpoint")
            snapshot = CheckpointStore(root).load(selected)
            if snapshot.project_id != project_id:
                raise ValueError("checkpoint belongs to another project")
            branch_snapshot = snapshot.model_copy(
                update={
                    "branch_id": branch_id,
                    "checkpoint_id": selected,
                    "request": snapshot.request.model_copy(
                        update={"branch_id": branch_id}
                    ),
                }
            )
            store = WikiStore(root, branch_id)
            store.mark_stale(selected, snapshot.current_step)
            await WikiBoundaryProcessor(
                root,
                consolidator=GatewayWikiConsolidator(gateway),
            ).rebuild(branch_snapshot)
            return store.view()
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Branch not found") from error
        except (OSError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/wiki/lint",
        response_model=WikiLintResult,
    )
    async def lint_wiki(project_id: str, branch_id: str) -> WikiLintResult:
        try:
            return WikiLinter(root_for(project_id), branch_id).run()
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Wiki not found") from error

    @router.get(
        "/projects/{project_id}/branches/{branch_id}/director-instructions",
        response_model=tuple[DirectorInstruction, ...],
    )
    async def list_director_instructions(
        project_id: str,
        branch_id: str,
    ) -> tuple[DirectorInstruction, ...]:
        return WikiStore(root_for(project_id), branch_id).list_instructions()

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
        root = root_for(project_id)
        checkpoint = CheckpointStore(root).load(request.checkpoint_id)
        if checkpoint.branch_id != branch_id or checkpoint.project_id != project_id:
            raise HTTPException(status_code=409, detail="checkpoint belongs elsewhere")
        instruction = DirectorInstruction(
            instruction_id=f"director:{uuid.uuid4().hex}",
            text=request.text,
            created_at=datetime.now(UTC),
            applies_from_checkpoint_id=request.checkpoint_id,
        )
        WikiStore(root, branch_id).add_instruction(instruction)
        return instruction

    return router
