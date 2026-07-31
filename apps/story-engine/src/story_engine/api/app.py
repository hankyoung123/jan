from collections.abc import Awaitable, Callable
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel

from story_engine import __version__
from story_engine.api.routes.projects import create_projects_router
from story_engine.config import EngineSettings


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class StatusResponse(BaseModel):
    status: str


AuthDependency = Callable[[str | None], Awaitable[None]]


def _auth_dependency(settings: EngineSettings) -> AuthDependency:
    async def require_session_token(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        scheme, separator, credentials = (authorization or "").partition(" ")
        valid_scheme = separator == " " and scheme == "Bearer"
        valid_token = bool(credentials) and compare_digest(
            credentials,
            settings.session_token,
        )
        if not valid_scheme or not valid_token:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid session token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    return require_session_token


def create_app(settings: EngineSettings | None = None) -> FastAPI:
    runtime_settings = settings or EngineSettings()
    require_session_token = _auth_dependency(runtime_settings)
    app = FastAPI(
        title="AI Story Evolution Engine",
        version=__version__,
        description="Local story-domain sidecar API",
    )
    app.state.settings = runtime_settings

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="story-engine",
            version=__version__,
        )

    @app.get(
        "/api/status",
        response_model=StatusResponse,
        dependencies=[Depends(require_session_token)],
        tags=["system"],
    )
    async def engine_status() -> StatusResponse:
        return StatusResponse(status="ready")

    app.include_router(
        create_projects_router(runtime_settings),
        dependencies=[Depends(require_session_token)],
    )

    return app
