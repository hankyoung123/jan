class ModelGatewayError(Exception):
    code = "model_gateway_error"


class ProfileNotFoundError(ModelGatewayError):
    code = "profile_not_found"


class ProfileMismatchError(ModelGatewayError):
    code = "profile_task_mismatch"


class ProviderNotFoundError(ModelGatewayError):
    code = "provider_not_found"


class MissingCredentialError(ModelGatewayError):
    code = "missing_credential"


class ModelTimeoutError(ModelGatewayError):
    code = "model_timeout"


class ProviderResponseError(ModelGatewayError):
    code = "provider_error"


class ResponseLimitError(ModelGatewayError):
    code = "response_limit_exceeded"


class StructuredOutputError(ModelGatewayError):
    code = "structured_output_invalid"


class ModelConfigurationError(ModelGatewayError):
    code = "model_configuration_error"
