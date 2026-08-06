from story_engine.models.contracts import ModelUsage


class ModelGatewayError(Exception):
    code = "model_gateway_error"


class ProfileNotFoundError(ModelGatewayError):
    code = "profile_not_found"


class ProfileMismatchError(ModelGatewayError):
    code = "profile_task_mismatch"


class ModelTimeoutError(ModelGatewayError):
    code = "model_timeout"


class ProviderResponseError(ModelGatewayError):
    code = "provider_error"


class UnsupportedResponseFormatError(ProviderResponseError):
    """Provider rejected the requested structured-output wire format."""


class ResponseLimitError(ModelGatewayError):
    code = "response_limit_exceeded"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.usage: ModelUsage | None = None
        self.retry_count = 0
        self.finish_reason: str | None = None
        self.max_tokens: int | None = None


class StructuredOutputError(ModelGatewayError):
    code = "structured_output_invalid"

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.usage: ModelUsage | None = None
        self.retry_count = 0


class ModelConfigurationError(ModelGatewayError):
    code = "model_configuration_error"
