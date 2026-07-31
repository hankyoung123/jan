import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from story_engine.api.model_errors import model_http_error
from story_engine.models.contracts import (
    ModelCatalog,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    UsageTotals,
)
from story_engine.models.errors import ModelGatewayError
from story_engine.models.gateway import ModelGateway
from story_engine.models.registry import ProfileRegistry


def _catalog(registry: ProfileRegistry) -> ModelCatalog:
    return ModelCatalog(profiles=registry.load().profiles)


def create_models_router(
    registry: ProfileRegistry,
    gateway: ModelGateway,
) -> APIRouter:
    router = APIRouter(tags=["models"])

    @router.get("/models/catalog", response_model=ModelCatalog)
    async def get_model_catalog() -> ModelCatalog:
        try:
            return _catalog(registry)
        except ModelGatewayError as error:
            raise model_http_error(error) from error

    @router.get("/models/profiles", response_model=list[ModelProfile])
    async def get_model_profiles() -> list[ModelProfile]:
        try:
            return list(registry.load().profiles)
        except ModelGatewayError as error:
            raise model_http_error(error) from error

    @router.put(
        "/models/profiles/{profile_id}",
        response_model=ModelProfile,
    )
    async def put_model_profile(
        profile_id: str,
        request: ModelProfile,
    ) -> ModelProfile:
        if request.id != profile_id:
            raise HTTPException(
                status_code=422, detail="profile ID does not match path"
            )
        try:
            registry.upsert_profile(request)
            return request
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
