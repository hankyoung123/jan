import json

from story_engine.contracts import build_openapi_schema


def test_openapi_contains_core_paths_and_bearer_security() -> None:
    schema = build_openapi_schema()

    assert {
        "/health",
        "/submissions/finalize",
        "/projects/{project_id}",
        "/projects/{project_id}/simulations",
        "/projects/{project_id}/simulations/{session_id}/step",
        "/projects/{project_id}/simulations/{session_id}/run",
        "/projects/{project_id}/simulations/{session_id}/checkpoint",
        "/projects/{project_id}/branches",
        "/projects/{project_id}/branches/{branch_id}/rollback",
        "/projects/{project_id}/branches/{branch_id}/projection",
        "/projects/{project_id}/branches/{branch_id}/narrative-sources",
        "/projects/{project_id}/branches/{branch_id}/manuscript/scenes",
        "/projects/{project_id}/branches/{branch_id}/manuscript/scenes/generate",
        "/projects/{project_id}/branches/{branch_id}/manuscript/scenes/{scene_id}",
        "/projects/{project_id}/branches/{branch_id}/manuscript/export",
        "/projects/{project_id}/branches/{branch_id}/wiki",
        "/projects/{project_id}/branches/{branch_id}/wiki/page",
        "/projects/{project_id}/branches/{branch_id}/wiki/rebuild",
        "/models/catalog",
        "/models/profiles",
        "/models/profiles/{profile_id}",
        "/models/complete",
        "/models/stream",
        "/models/usage",
    } <= set(schema["paths"])
    assert "/models/providers" not in schema["paths"]
    assert "/models/providers/{provider_id}" not in schema["paths"]
    assert not any("/turns" in path for path in schema["paths"])
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


def test_openapi_model_profile_exposes_only_jan_model_reference() -> None:
    schema = build_openapi_schema()

    profile_schema = schema["components"]["schemas"]["ModelProfile"]
    properties = profile_schema["properties"]

    assert "model_ref" in properties
    assert "provider_id" not in properties
    assert "reasoning_effort" not in properties
