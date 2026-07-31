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

