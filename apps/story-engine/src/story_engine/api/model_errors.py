from fastapi import HTTPException, status

from story_engine.models.errors import (
    ModelConfigurationError,
    ModelGatewayError,
    ModelTimeoutError,
    ProfileMismatchError,
    ProfileNotFoundError,
    ProviderResponseError,
    ResponseLimitError,
    StructuredOutputError,
)


def model_http_error(error: ModelGatewayError) -> HTTPException:
    if isinstance(error, ProfileNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, ModelTimeoutError):
        code = status.HTTP_504_GATEWAY_TIMEOUT
    elif isinstance(error, ProviderResponseError):
        code = status.HTTP_502_BAD_GATEWAY
    elif isinstance(
        error,
        (
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
