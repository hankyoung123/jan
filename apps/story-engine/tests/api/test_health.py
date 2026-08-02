from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings


def test_health_returns_exact_public_contract() -> None:
    client = TestClient(create_app(EngineSettings(session_token="test-token")))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "story-engine",
        "version": "0.1.0",
    }


def test_health_allows_only_configured_desktop_origin() -> None:
    client = TestClient(create_app(EngineSettings(session_token="test-token")))

    allowed = client.get(
        "/health",
        headers={"Origin": "http://127.0.0.1:1420"},
    )
    unknown = client.get(
        "/health",
        headers={"Origin": "https://untrusted.example"},
    )

    assert allowed.headers["access-control-allow-origin"] == ("http://127.0.0.1:1420")
    assert "access-control-allow-origin" not in unknown.headers
