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


class ResponseLimitError(ModelGatewayError):
    code = "response_limit_exceeded"


class StructuredOutputError(ModelGatewayError):
    code = "structured_output_invalid"

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ModelConfigurationError(ModelGatewayError):
    code = "model_configuration_error"
