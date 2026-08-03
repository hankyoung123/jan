import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.domain.models import Character
from story_engine.models.contracts import ModelProfile, ModelStreamChunk
from story_engine.models.registry import ProfileRegistry
from story_engine.submission.service import fog_harbor_submission
from story_engine.workspace.project_store import ProjectStore

AUTH = {"Authorization": "Bearer test-token"}


class PromotionTransport:
    def __init__(self) -> None:
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(payload)
        output = {
            "review": {
                "mode": "promotion_review",
                "passed": True,
                "summary": "该人物已经形成独立目标并可能主动影响后续局势。",
                "issues": [],
            },
            "proposed_goal": "主动引导客船避开近港暗礁",
        }
        return {
            "choices": [
                {
                    "message": {"content": json.dumps(output, ensure_ascii=False)},
                    "finish_reason": "stop",
                }
            ]
        }

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> AsyncIterator[ModelStreamChunk]:
        del payload, timeout_seconds
        if False:
            yield ModelStreamChunk()
        raise AssertionError("promotion review does not stream")


def _client(tmp_path: Path) -> tuple[TestClient, PromotionTransport]:
    transport = PromotionTransport()
    registry = ProfileRegistry(tmp_path / "models.json")
    registry.upsert_profile(
        ModelProfile(
            id="editor",
            task_type="editor",
            model_ref="test-provider/test-editor",
        )
    )
    client = TestClient(
        create_app(
            EngineSettings(
                session_token="test-token",
                projects_root=tmp_path,
                model_registry_path=registry.path,
            ),
            model_registry=registry,
            model_transport=transport,
        )
    )
    created = client.post(
        "/submissions/finalize",
        headers=AUTH,
        json=fog_harbor_submission().model_dump(mode="json"),
    )
    assert created.status_code == 201
    ProjectStore(tmp_path / "fog-harbor").save_character(
        Character(
            id="temporary-pilot",
            display_name="临时引航员",
            type="npc",
            identity="暴风雨中赶到港口的引航员",
            core_desire="让客船安全避开暗礁",
            current_goal="观察近港水流",
            known_fact_ids=("fact:near-harbor-reefs",),
            version=2,
        ),
        overwrite=False,
    )
    return client, transport


def test_character_routes_review_then_explicitly_confirm_promotion(
    tmp_path: Path,
) -> None:
    client, transport = _client(tmp_path)

    listed = client.get("/projects/fog-harbor/characters", headers=AUTH)
    detail = client.get(
        "/projects/fog-harbor/characters/temporary-pilot",
        headers=AUTH,
    )
    reviewed = client.post(
        "/projects/fog-harbor/characters/temporary-pilot/promotion-review?branch_id=main",
        headers=AUTH,
    )

    assert listed.status_code == 200
    assert len(listed.json()) == 3
    assert detail.json()["type"] == "npc"
    assert reviewed.status_code == 200
    candidate = reviewed.json()["candidate"]
    assert candidate["id"] == "promotion-temporary-pilot-v2"
    assert candidate["status"] == "pending"
    assert (
        client.get(
            "/projects/fog-harbor/characters/temporary-pilot",
            headers=AUTH,
        ).json()["type"]
        == "npc"
    )
    assert len(transport.calls) == 1

    promoted = client.post(
        "/projects/fog-harbor/characters/temporary-pilot/promote",
        headers=AUTH,
        json={"candidate_id": candidate["id"]},
    )

    assert promoted.status_code == 200
    assert promoted.json()["candidate"]["status"] == "committed"
    assert promoted.json()["character"]["type"] == "active"
    assert "event" not in promoted.json()
    assert (
        client.get(
            "/projects/fog-harbor/characters/temporary-pilot",
            headers=AUTH,
        ).json()["type"]
        == "active"
    )


def test_promotion_requires_matching_review_candidate(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)

    missing = client.post(
        "/projects/fog-harbor/characters/temporary-pilot/promote",
        headers=AUTH,
        json={"candidate_id": "promotion-temporary-pilot-v2"},
    )

    assert missing.status_code == 404


def test_active_character_cannot_receive_promotion_review(tmp_path: Path) -> None:
    client, transport = _client(tmp_path)

    response = client.post(
        "/projects/fog-harbor/characters/chen-mo/promotion-review?branch_id=main",
        headers=AUTH,
    )

    assert response.status_code == 409
    assert "only an NPC" in response.json()["detail"]
    assert transport.calls == []
