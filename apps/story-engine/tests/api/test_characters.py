from pathlib import Path

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.domain.models import Character
from story_engine.submission.service import fog_harbor_submission

AUTH = {"Authorization": "Bearer test-token"}


def _client(tmp_path: Path) -> TestClient:
    client = TestClient(
        create_app(
            EngineSettings(
                session_token="test-token",
                projects_root=tmp_path,
                model_registry_path=tmp_path / "models.json",
            )
        )
    )
    created = client.post(
        "/submissions/finalize",
        headers=AUTH,
        json=fog_harbor_submission().model_dump(mode="json"),
    )
    assert created.status_code == 201
    return client


def test_character_routes_read_branch_seed_state(tmp_path: Path) -> None:
    client = _client(tmp_path)

    listed = client.get(
        "/projects/fog-harbor/characters?branch_id=main",
        headers=AUTH,
    )
    detail = client.get(
        "/projects/fog-harbor/characters/chen-mo?branch_id=main",
        headers=AUTH,
    )

    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()} == {"chen-mo", "lin-lan"}
    assert detail.status_code == 200
    assert detail.json()["type"] == "active"


def test_character_routes_reject_incompatible_project_state_cleanly(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    character_path = tmp_path / "fog-harbor/characters/active/chen-mo.md"
    content = character_path.read_text(encoding="utf-8")
    character_path.write_text(
        content.replace("version: 0", "removed_field: legacy\nversion: 0", 1),
        encoding="utf-8",
    )

    response = client.get(
        "/projects/fog-harbor/characters",
        headers={**AUTH, "Origin": "http://tauri.localhost"},
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "Project character state is incompatible with this version"
    }
    assert response.headers["access-control-allow-origin"] == "http://tauri.localhost"


def test_manual_promotion_endpoints_do_not_exist(tmp_path: Path) -> None:
    client = _client(tmp_path)

    review = client.post(
        "/projects/fog-harbor/characters/chen-mo/promotion-review?branch_id=main",
        headers=AUTH,
    )
    confirm = client.post(
        "/projects/fog-harbor/characters/chen-mo/promote",
        headers=AUTH,
        json={"candidate_id": "promotion-chen-mo-v0"},
    )

    assert review.status_code == 404
    assert confirm.status_code == 404


def test_character_contract_still_allows_non_agent_npcs() -> None:
    npc = Character(
        id="temporary-pilot",
        display_name="临时引航员",
        type="npc",
        identity="暴风雨中赶到港口的引航员",
        core_desire="让客船安全避开暗礁",
    )

    assert npc.current_goal is None
