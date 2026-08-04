from pathlib import Path

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.submission.service import SubmissionService, fog_harbor_submission

TOKEN = "test-token"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}


def _client(tmp_path: Path) -> TestClient:
    settings = EngineSettings(
        session_token=TOKEN,
        projects_root=tmp_path / "projects",
        model_registry_path=tmp_path / "config" / "models.json",
        model_base_url=None,
        model_api_key=None,
    )
    SubmissionService(settings.projects_root).finalize(fog_harbor_submission())
    return TestClient(create_app(settings))


def test_wiki_page_manual_edit_conflicts_with_stale_revision(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    page = client.get(
        "/projects/fog-harbor/branches/main/wiki/page?path=world%2Fstate.md",
        headers=HEADERS,
    )
    assert page.status_code == 200
    body = page.json()

    saved = client.put(
        "/projects/fog-harbor/branches/main/wiki/page",
        headers=HEADERS,
        json={
            "path": body["path"],
            "content": "# Current World State\n\nEdited by hand.",
            "expected_revision": body["revision"],
        },
    )

    assert saved.status_code == 200
    assert saved.json()["revision"] == body["revision"] + 1
    assert len(saved.json()["content_hash"]) == 64

    conflict = client.put(
        "/projects/fog-harbor/branches/main/wiki/page",
        headers=HEADERS,
        json={
            "path": body["path"],
            "content": "# Current World State\n\nStale base.",
            "expected_revision": body["revision"],
        },
    )

    assert conflict.status_code == 409
    assert "current revision" in conflict.json()["detail"]
