from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.domain.models import StoryEvent
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.event_store import EventStore

AUTH = {"Authorization": "Bearer test-token"}


def _client(tmp_path: Path) -> TestClient:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    EventStore(tmp_path / "fog-harbor").append(
        StoryEvent(
            id="event-000001",
            sequence=1,
            occurred_at=datetime(2026, 7, 31, 4, 0, tzinfo=UTC),
            summary="陈默在灯塔里发现了一道新鲜刮痕。",
            participants=("chen-mo",),
            source_record_id="session:one",
            approved_by_user=True,
        )
    )
    return TestClient(
        create_app(EngineSettings(session_token="test-token", projects_root=tmp_path))
    )


def test_rag_api_rebuilds_and_returns_cited_writer_evidence(tmp_path: Path) -> None:
    client = _client(tmp_path)

    rebuilt = client.post("/projects/fog-harbor/rag/rebuild", headers=AUTH)
    searched = client.post(
        "/projects/fog-harbor/rag/search",
        headers=AUTH,
        json={
            "exact_id": "event-000001",
            "scope": {"kind": "writer"},
            "limit": 10,
        },
    )

    assert rebuilt.status_code == 200
    assert rebuilt.json()["project_id"] == "fog-harbor"
    assert rebuilt.json()["document_count"] >= 5
    assert rebuilt.json()["chunk_count"] > rebuilt.json()["document_count"]
    assert searched.status_code == 200
    assert searched.json()["permission_scope"] == "writer"
    assert searched.json()["hits"]
    assert all(
        hit["source_id"] == "event-000001"
        and hit["source_path"] == "events/000001.md"
        and hit["chunk_id"].startswith("event-000001#")
        for hit in searched.json()["hits"]
    )
    assert "备用钥匙" not in str(searched.json()["hits"])


def test_rag_api_enforces_character_scope_and_authentication(tmp_path: Path) -> None:
    client = _client(tmp_path)

    unauthenticated = client.post(
        "/projects/fog-harbor/rag/search",
        json={"query": "灯塔", "scope": {"kind": "editorial"}},
    )
    other_card = client.post(
        "/projects/fog-harbor/rag/search",
        headers=AUTH,
        json={
            "exact_id": "lin-lan",
            "scope": {"kind": "character", "character_id": "chen-mo"},
        },
    )
    invalid_scope = client.post(
        "/projects/fog-harbor/rag/search",
        headers=AUTH,
        json={"query": "灯塔", "scope": {"kind": "character"}},
    )

    assert unauthenticated.status_code == 401
    assert other_card.status_code == 200
    assert other_card.json()["hits"] == []
    assert invalid_scope.status_code == 422
