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
        "/projects/{project_id}/simulations/{session_id}/projections",
        "/projects/{project_id}/simulations/{session_id}/projections/{task_id}/retry",
        "/projects/{project_id}/branches",
        "/projects/{project_id}/branches/{branch_id}/rollback",
        "/projects/{project_id}/branches/{branch_id}/manuscript/sources",
        "/projects/{project_id}/branches/{branch_id}/manuscript/scenes",
        "/projects/{project_id}/branches/{branch_id}/manuscript/scenes/{scene_id}",
        "/projects/{project_id}/branches/{branch_id}/manuscript/export",
        "/projects/{project_id}/branches/{branch_id}/wiki",
        "/projects/{project_id}/branches/{branch_id}/wiki/page",
        "/projects/{project_id}/branches/{branch_id}/wiki/rebuild",
        "/agent-profiles/catalog",
        "/agent-profiles",
        "/agent-profiles/{agent_type}",
        "/models/complete",
        "/models/stream",
        "/models/usage",
    } <= set(schema["paths"])
    assert (
        "/projects/{project_id}/simulations/{session_id}/maintenance/retry"
        not in schema["paths"]
    )
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
    rebuild = schema["paths"][
        "/projects/{project_id}/branches/{branch_id}/wiki/rebuild"
    ]["post"]
    assert "requestBody" not in rebuild
    assert "WikiRebuildRequest" not in schema["components"]["schemas"]


def test_openapi_never_contains_runtime_session_token() -> None:
    serialized = json.dumps(build_openapi_schema())

    assert "contract-generation-token" not in serialized


def test_openapi_agent_profile_contains_behavior_and_model_configuration() -> None:
    schema = build_openapi_schema()

    profile_schema = schema["components"]["schemas"]["AgentProfile"]
    properties = profile_schema["properties"]

    assert "model" in properties
    assert "agent_type" in properties
    assert "default_system_prompt" in properties
    assert "provider_id" not in properties
    assert "reasoning_effort" in properties
