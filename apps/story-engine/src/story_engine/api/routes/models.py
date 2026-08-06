import json
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from story_engine.api.model_errors import model_http_error
from story_engine.models.contracts import (
    AgentProfile,
    AgentProfileCatalog,
    AgentProfilePatch,
    AgentType,
    ModelRequest,
    ModelResponse,
    UsageTotals,
)
from story_engine.models.errors import ModelGatewayError
from story_engine.models.gateway import ModelGateway
from story_engine.models.registry import ProfileRegistry


def _catalog(registry: ProfileRegistry) -> AgentProfileCatalog:
    return AgentProfileCatalog(profiles=registry.load().profiles)


def create_models_router(
    registry: ProfileRegistry,
    gateway: ModelGateway,
    projects_root: Path,
) -> APIRouter:
    del projects_root
    router = APIRouter(tags=["models"])

    @router.get("/agent-profiles/catalog", response_model=AgentProfileCatalog)
    async def get_agent_catalog() -> AgentProfileCatalog:
        try:
            return _catalog(registry)
        except ModelGatewayError as error:
            raise model_http_error(error) from error

    @router.get("/agent-profiles", response_model=list[AgentProfile])
    async def get_agent_profiles() -> list[AgentProfile]:
        try:
            return list(registry.load().profiles)
        except ModelGatewayError as error:
            raise model_http_error(error) from error

    @router.patch(
        "/agent-profiles/{agent_type}",
        response_model=AgentProfile,
    )
    async def patch_agent_profile(
        agent_type: AgentType,
        request: AgentProfilePatch,
    ) -> AgentProfile:
        if not request.model_fields_set:
            raise HTTPException(status_code=422, detail="empty profile patch")
        try:
            return registry.patch_profile(agent_type, request)
        except ModelGatewayError as error:
            raise model_http_error(error) from error

    @router.post("/models/complete", response_model=ModelResponse)
    async def complete_model(request: ModelRequest) -> ModelResponse:
        try:
            return await gateway.complete(request)
        except ModelGatewayError as error:
            raise model_http_error(error) from error

    @router.post("/models/stream", response_class=StreamingResponse)
    async def stream_model(request: ModelRequest) -> StreamingResponse:
        async def events() -> AsyncIterator[str]:
            try:
                async for chunk in gateway.stream(request):
                    data = json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False)
                    yield f"data: {data}\n\n"
            except ModelGatewayError as error:
                data = json.dumps(
                    {"code": error.code, "message": str(error)},
                    ensure_ascii=False,
                )
                yield f"event: error\ndata: {data}\n\n"

        return StreamingResponse(events(), media_type="text/event-stream")

    @router.get("/models/usage", response_model=UsageTotals)
    async def model_usage() -> UsageTotals:
        return gateway.usage.totals()

    return router
