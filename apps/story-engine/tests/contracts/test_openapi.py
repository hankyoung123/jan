import json

from story_engine.contracts import build_openapi_schema


def test_openapi_contains_core_paths_and_bearer_security() -> None:
    schema = build_openapi_schema()

    assert {
        "/health",
        "/submissions/finalize",
        "/projects/{project_id}",
        "/projects/{project_id}/turns/generate",
        "/projects/{project_id}/turns/{turn_id}/request-revision",
        "/projects/{project_id}/turns/{turn_id}/confirm",
        "/projects/{project_id}/turns/{turn_id}/discard",
        "/projects/{project_id}/scenes",
        "/projects/{project_id}/scenes/generate",
        "/projects/{project_id}/scenes/{scene_id}",
        "/projects/{project_id}/scenes/{scene_id}/amendments/{amendment_id}/confirm",
        "/projects/{project_id}/manuscript/export",
        "/models/catalog",
        "/models/profiles",
        "/models/profiles/{profile_id}",
        "/models/complete",
        "/models/stream",
        "/models/usage",
    } <= set(schema["paths"])
    assert "/models/providers" not in schema["paths"]
    assert "/models/providers/{provider_id}" not in schema["paths"]
    assert "/projects/{project_id}/turns" not in schema["paths"]
    assert "/projects/{project_id}/turns/{turn_id}/approve" not in schema["paths"]
    assert schema["components"]["securitySchemes"]["SessionToken"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert "security" not in schema["paths"]["/health"]["get"]
    assert schema["paths"]["/submissions/finalize"]["post"]["security"] == [
        {"SessionToken": []}
    ]


def test_openapi_never_contains_runtime_session_token() -> None:
    serialized = json.dumps(build_openapi_schema())

    assert "contract-generation-token" not in serialized


def test_openapi_model_profile_exposes_reasoning_effort_choices() -> None:
    schema = build_openapi_schema()

    profile_schema = schema["components"]["schemas"]["ModelProfile"]
    reasoning = profile_schema["properties"]["reasoning_effort"]

    assert reasoning["default"] == "disabled"
    assert reasoning["enum"] == ["disabled", "low", "high", "max"]
