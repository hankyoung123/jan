from story_engine.domain.trace import ModelCallTrace
from story_engine.models.contracts import (
    AgentProfile,
    AgentProfilePatch,
    ModelRequest,
    ModelResponse,
)
from story_engine.models.limits import (
    MAX_MODEL_OUTPUT_TOKENS,
    MAX_MODEL_TIMEOUT_SECONDS,
)


def _maximum(model: type, field_name: str) -> int:
    field_schema = model.model_json_schema()["properties"][field_name]
    if "maximum" in field_schema:
        return field_schema["maximum"]
    return next(
        variant["maximum"]
        for variant in field_schema["anyOf"]
        if "maximum" in variant
    )


def test_model_configuration_limits_are_consistent() -> None:
    assert _maximum(AgentProfile, "timeout_seconds") == MAX_MODEL_TIMEOUT_SECONDS
    assert _maximum(AgentProfilePatch, "timeout_seconds") == MAX_MODEL_TIMEOUT_SECONDS
    assert _maximum(ModelRequest, "timeout_seconds") == MAX_MODEL_TIMEOUT_SECONDS
    assert _maximum(ModelCallTrace, "timeout_seconds") == MAX_MODEL_TIMEOUT_SECONDS

    assert _maximum(AgentProfile, "max_output_tokens") == MAX_MODEL_OUTPUT_TOKENS
    assert (
        _maximum(AgentProfilePatch, "max_output_tokens")
        == MAX_MODEL_OUTPUT_TOKENS
    )
    assert _maximum(ModelRequest, "max_output_tokens") == MAX_MODEL_OUTPUT_TOKENS
    assert _maximum(ModelResponse, "max_tokens") == MAX_MODEL_OUTPUT_TOKENS
    assert _maximum(ModelCallTrace, "max_tokens") == MAX_MODEL_OUTPUT_TOKENS
