import json

from story_engine.contracts import build_openapi_schema


def test_openapi_contains_core_paths_and_bearer_security() -> None:
    schema = build_openapi_schema()

    assert {
        "/health",
        "/projects",
        "/projects/{project_id}",
        "/projects/{project_id}/turns",
        "/projects/{project_id}/turns/{turn_id}/approve",
    } <= set(schema["paths"])
    assert schema["components"]["securitySchemes"]["SessionToken"] == {
        "type": "http",
        "scheme": "bearer",
    }
    assert "security" not in schema["paths"]["/health"]["get"]
    assert schema["paths"]["/projects"]["post"]["security"] == [
        {"SessionToken": []}
    ]


def test_openapi_never_contains_runtime_session_token() -> None:
    serialized = json.dumps(build_openapi_schema())

    assert "contract-generation-token" not in serialized

