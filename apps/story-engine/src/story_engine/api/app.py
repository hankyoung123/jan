from collections.abc import Awaitable, Callable
from secrets import compare_digest
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
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


AuthDependency = Callable[
    [HTTPAuthorizationCredentials | None],
    Awaitable[None],
]


def _auth_dependency(settings: EngineSettings) -> AuthDependency:
    bearer = HTTPBearer(auto_error=False, scheme_name="SessionToken")

    async def require_session_token(
        credentials: Annotated[
            HTTPAuthorizationCredentials | None,
            Depends(bearer),
        ] = None,
    ) -> None:
        valid_scheme = (
            credentials is not None and credentials.scheme.lower() == "bearer"
        )
        valid_token = credentials is not None and compare_digest(
            credentials.credentials,
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
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(runtime_settings.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

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
