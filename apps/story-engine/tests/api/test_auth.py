from threading import Event

from fastapi.testclient import TestClient
from pytest import mark

from story_engine.api.app import create_app
from story_engine.config import EngineSettings


@mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": "Basic test-token"},
    ],
)
def test_protected_status_rejects_missing_or_invalid_token(
    headers: dict[str, str],
) -> None:
    client = TestClient(create_app(EngineSettings(session_token="test-token")))

    response = client.get("/api/status", headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid session token"}


def test_protected_status_accepts_valid_token() -> None:
    client = TestClient(create_app(EngineSettings(session_token="test-token")))

    response = client.get(
        "/api/status",
        headers={"Authorization": "Bearer test-token"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_managed_shutdown_requires_auth_and_schedules_server_exit() -> None:
    requested = Event()
    client = TestClient(
        create_app(
            EngineSettings(session_token="test-token"),
            shutdown_request=requested.set,
        )
    )

    unauthorized = client.post("/internal/shutdown")
    accepted = client.post(
        "/internal/shutdown",
        headers={"Authorization": "Bearer test-token"},
    )

    assert unauthorized.status_code == 401
    assert accepted.status_code == 202
    assert accepted.json() == {"status": "shutting_down"}
    assert requested.is_set()
