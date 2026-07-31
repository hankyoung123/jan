import asyncio
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, status
from pydantic import JsonValue

from story_engine.api.model_errors import model_http_error
from story_engine.concordia_adapter import ConcordiaStoryAdapter
from story_engine.config import EngineSettings
from story_engine.domain.errors import DomainError, InvalidTransitionError
from story_engine.domain.models import TurnCandidate
from story_engine.events.commit import CommitResult
from story_engine.events.stream import EngineEventBus, EngineEventType
from story_engine.evolution.service import (
    EvolutionService,
    RevisionRequest,
    TurnGenerationRequest,
)
from story_engine.models.errors import ModelGatewayError
from story_engine.models.gateway import ModelGateway
from story_engine.submission.service import (
    SubmissionConversationRequest,
    SubmissionConversationResponse,
    SubmissionDiscussionService,
    SubmissionPackage,
    SubmissionService,
)
from story_engine.workspace.candidate_store import CandidateStore
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectSnapshot, ProjectStore

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
    event_bus: EngineEventBus,
    model_gateway: ModelGateway,
) -> APIRouter:
    router = APIRouter(tags=["projects"])

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
            return await SubmissionDiscussionService(model_gateway).respond(request)
        except ModelGatewayError as error:
            raise model_http_error(error) from error
        except (DomainError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/submissions/finalize",
        response_model=ProjectSnapshot,
        status_code=status.HTTP_201_CREATED,
    )
    async def finalize_submission(package: SubmissionPackage) -> ProjectSnapshot:
        settings.projects_root.mkdir(parents=True, exist_ok=True)
        try:
            return SubmissionService(settings.projects_root).finalize(package)
        except FileExistsError as error:
            raise HTTPException(
                status_code=409,
                detail="Project already exists",
            ) from error
        except DomainError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/projects/{project_id}", response_model=ProjectSnapshot)
    async def get_project(project_id: str) -> ProjectSnapshot:
        return ProjectStore(_require_project(settings, project_id)).load()

    @router.post(
        "/projects/{project_id}/turns/generate",
        response_model=TurnCandidate,
        status_code=status.HTTP_201_CREATED,
    )
    async def generate_turn(
        project_id: str,
        request: TurnGenerationRequest,
    ) -> TurnCandidate:
        root = _require_project(settings, project_id)
        active_turn_id: str | None = None
        try:
            participants = request.participant_ids or None

            def emit(
                event_type: EngineEventType,
                payload: dict[str, JsonValue],
            ) -> None:
                nonlocal active_turn_id
                active_turn_id = str(payload["turn_id"])
                event_bus.publish(
                    project_id=project_id,
                    turn_id=active_turn_id,
                    event_type=event_type,
                    payload=payload,
                )

            service = EvolutionService(
                root,
                generator=ConcordiaStoryAdapter(model_gateway),
            )
            return await asyncio.to_thread(
                service.generate_turn,
                participants,
                event_sink=emit,
            )
        except (ModelGatewayError, DomainError, ValueError) as error:
            if active_turn_id is not None:
                event_bus.publish(
                    project_id=project_id,
                    turn_id=active_turn_id,
                    event_type="turn.failed",
                    payload={"turn_id": active_turn_id, "reason": str(error)},
                )
            if isinstance(error, ModelGatewayError):
                raise model_http_error(error) from error
            raise HTTPException(status_code=409, detail=str(error)) from error

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
        "/projects/{project_id}/turns/{turn_id}/confirm",
        response_model=CommitResult,
    )
    async def confirm_turn(project_id: str, turn_id: str) -> CommitResult:
        root = _require_project(settings, project_id)
        try:
            return EvolutionService(
                root,
                generator=ConcordiaStoryAdapter(model_gateway),
            ).confirm(turn_id)
        except FileNotFoundError as error:
            raise HTTPException(status_code=404, detail="Turn not found") from error
        except (DomainError, InvalidTransitionError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.post(
        "/projects/{project_id}/turns/{turn_id}/request-revision",
        response_model=TurnCandidate,
    )
    async def request_turn_revision(
        project_id: str,
        turn_id: str,
        request: RevisionRequest,
    ) -> TurnCandidate:
        root = _require_project(settings, project_id)
        try:
            return EvolutionService(
                root,
                generator=ConcordiaStoryAdapter(model_gateway),
            ).request_revision(
                turn_id,
                request.instruction,
            )
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
        try:
            candidate = EvolutionService(
                root,
                generator=ConcordiaStoryAdapter(model_gateway),
            ).discard(turn_id)
            event_bus.publish(
                project_id=project_id,
                turn_id=turn_id,
                event_type="turn.cancelled",
                payload={"turn_id": turn_id},
            )
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
