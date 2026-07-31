import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, status

from story_engine.config import EngineSettings
from story_engine.domain.errors import DomainError, InvalidTransitionError
from story_engine.domain.models import ReviewResult, TurnCandidate
from story_engine.events.commit import CommitResult, EventCommitService
from story_engine.workspace.candidate_store import CandidateStore
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import (
    ProjectSeed,
    ProjectSnapshot,
    ProjectStore,
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


def create_projects_router(settings: EngineSettings) -> APIRouter:
    router = APIRouter(tags=["projects"])

    @router.post(
        "/projects",
        response_model=ProjectSnapshot,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_project(seed: ProjectSeed) -> ProjectSnapshot:
        settings.projects_root.mkdir(parents=True, exist_ok=True)
        try:
            return ProjectStore(_project_root(settings, seed.id)).create(seed)
        except FileExistsError as error:
            raise HTTPException(
                status_code=409,
                detail="Project already exists",
            ) from error

    @router.get("/projects/{project_id}", response_model=ProjectSnapshot)
    async def get_project(project_id: str) -> ProjectSnapshot:
        return ProjectStore(_require_project(settings, project_id)).load()

    @router.post(
        "/projects/{project_id}/turns",
        response_model=TurnCandidate,
        status_code=status.HTTP_201_CREATED,
    )
    async def create_turn(
        project_id: str,
        candidate: TurnCandidate,
    ) -> TurnCandidate:
        root = _require_project(settings, project_id)
        if candidate.project_id != project_id:
            raise HTTPException(status_code=409, detail="Turn project mismatch")
        try:
            CandidateStore(root).save(candidate, overwrite=False)
        except FileExistsError as error:
            raise HTTPException(
                status_code=409,
                detail="Turn already exists",
            ) from error
        return candidate

    @router.get(
        "/projects/{project_id}/turns/{turn_id}",
        response_model=TurnCandidate,
    )
    async def get_turn(project_id: str, turn_id: str) -> TurnCandidate:
        root = _require_project(settings, project_id)
        try:
            return CandidateStore(root).load(turn_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Turn not found") from error

    @router.post(
        "/projects/{project_id}/turns/{turn_id}/review",
        response_model=TurnCandidate,
    )
    async def review_turn(
        project_id: str,
        turn_id: str,
        review: ReviewResult,
    ) -> TurnCandidate:
        root = _require_project(settings, project_id)
        store = CandidateStore(root)
        try:
            candidate = store.load(turn_id).with_review(review)
            store.save(candidate)
            return candidate
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Turn not found") from error
        except InvalidTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/turns/{turn_id}/approve",
        response_model=CommitResult,
    )
    async def approve_turn(project_id: str, turn_id: str) -> CommitResult:
        root = _require_project(settings, project_id)
        store = CandidateStore(root)
        try:
            approved = store.load(turn_id).approve()
            result = EventCommitService(root).commit(approved)
            store.save(result.candidate)
            return result
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Turn not found") from error
        except (DomainError, InvalidTransitionError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/turns/{turn_id}/discard",
        response_model=TurnCandidate,
    )
    async def discard_turn(project_id: str, turn_id: str) -> TurnCandidate:
        root = _require_project(settings, project_id)
        store = CandidateStore(root)
        try:
            candidate = store.load(turn_id).discard()
            store.save(candidate)
            return candidate
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Turn not found") from error
        except InvalidTransitionError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/projects/{project_id}/events")
    async def list_events(project_id: str) -> tuple[object, ...]:
        root = _require_project(settings, project_id)
        return EventStore(root).list_events()

    return router
