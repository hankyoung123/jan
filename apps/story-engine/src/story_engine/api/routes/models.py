import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import Field, SecretStr

from story_engine.domain.models import DomainModel
from story_engine.models.contracts import (
    ModelCatalog,
    ModelProfile,
    ModelRequest,
    ModelResponse,
    ProviderConfig,
    ProviderKind,
    ProviderView,
    UsageTotals,
)
from story_engine.models.errors import (
    MissingCredentialError,
    ModelConfigurationError,
    ModelGatewayError,
    ModelTimeoutError,
    ProfileMismatchError,
    ProfileNotFoundError,
    ProviderNotFoundError,
    ProviderResponseError,
    ResponseLimitError,
    StructuredOutputError,
)
from story_engine.models.gateway import ModelGateway
from story_engine.models.registry import ProfileRegistry
from story_engine.models.secrets import SecretStore


class ProviderWrite(DomainModel):
    name: str = Field(min_length=1, max_length=80)
    kind: ProviderKind
    base_url: str = Field(min_length=1, max_length=2048)
    requires_api_key: bool = False
    api_key: SecretStr | None = None
    clear_api_key: bool = False


def _provider_view(
    provider: ProviderConfig,
    secrets: SecretStore,
) -> ProviderView:
    return ProviderView(
        **provider.model_dump(mode="json"),
        has_api_key=secrets.get(provider.id) is not None,
    )


def _catalog(registry: ProfileRegistry, secrets: SecretStore) -> ModelCatalog:
    state = registry.load()
    return ModelCatalog(
        providers=tuple(
            _provider_view(provider, secrets) for provider in state.providers
        ),
        profiles=state.profiles,
    )


def _http_error(error: ModelGatewayError) -> HTTPException:
    if isinstance(error, (ProfileNotFoundError, ProviderNotFoundError)):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, ModelTimeoutError):
        code = status.HTTP_504_GATEWAY_TIMEOUT
    elif isinstance(error, ProviderResponseError):
        code = status.HTTP_502_BAD_GATEWAY
    elif isinstance(
        error,
        (
            MissingCredentialError,
            ModelConfigurationError,
            ProfileMismatchError,
            ResponseLimitError,
            StructuredOutputError,
        ),
    ):
        code = status.HTTP_422_UNPROCESSABLE_CONTENT
    else:
        code = status.HTTP_500_INTERNAL_SERVER_ERROR
    return HTTPException(
        status_code=code,
        detail={"code": error.code, "message": str(error)},
    )


def create_models_router(
    registry: ProfileRegistry,
    secrets: SecretStore,
    gateway: ModelGateway,
) -> APIRouter:
    router = APIRouter(tags=["models"])

    @router.get("/models/catalog", response_model=ModelCatalog)
    async def get_model_catalog() -> ModelCatalog:
        try:
            return _catalog(registry, secrets)
        except ModelGatewayError as error:
            raise _http_error(error) from error

    @router.get("/models/providers", response_model=list[ProviderView])
    async def get_model_providers() -> list[ProviderView]:
        try:
            return [
                _provider_view(provider, secrets)
                for provider in registry.load().providers
            ]
        except ModelGatewayError as error:
            raise _http_error(error) from error

    @router.get("/models/profiles", response_model=list[ModelProfile])
    async def get_model_profiles() -> list[ModelProfile]:
        try:
            return list(registry.load().profiles)
        except ModelGatewayError as error:
            raise _http_error(error) from error

    @router.put(
        "/models/providers/{provider_id}",
        response_model=ProviderView,
    )
    async def put_model_provider(
        provider_id: str,
        request: ProviderWrite,
    ) -> ProviderView:
        try:
            provider = ProviderConfig(
                id=provider_id,
                name=request.name,
                kind=request.kind,
                base_url=request.base_url,
                requires_api_key=request.requires_api_key,
            )
            registry.upsert_provider(provider)
            if request.clear_api_key:
                secrets.delete(provider.id)
            elif request.api_key is not None:
                secrets.set(provider.id, request.api_key.get_secret_value())
            return _provider_view(provider, secrets)
        except ModelGatewayError as error:
            raise _http_error(error) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

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
            raise _http_error(error) from error

    @router.post("/models/complete", response_model=ModelResponse)
    async def complete_model(request: ModelRequest) -> ModelResponse:
        try:
            return await gateway.complete(request)
        except ModelGatewayError as error:
            raise _http_error(error) from error

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
