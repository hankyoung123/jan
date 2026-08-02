import json
import re
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.models.contracts import ModelStreamChunk
from story_engine.submission.service import SubmissionService, fog_harbor_submission

AUTH = {"Authorization": "Bearer integration-token"}


class ReplayGatewayTransport:
    """Prompt-addressed replay that still exercises ModelGateway validation."""

    def __init__(self, *, entity_change: str | None = None) -> None:
        self.calls: list[str] = []
        self.entity_change = entity_change

    @staticmethod
    def _choice(prompt: str, payload: Mapping[str, Any]) -> str:
        response_format = payload.get("response_format")
        schema = (
            response_format.get("json_schema", {}).get("schema", {})
            if isinstance(response_format, Mapping)
            else {}
        )
        properties = schema.get("properties", {})
        actor_ids = (
            properties.get("actor_ids") if isinstance(properties, Mapping) else None
        )
        if isinstance(actor_ids, Mapping):
            items = actor_ids.get("items", {})
            candidates = items.get("enum", []) if isinstance(items, Mapping) else []
            selected = [candidate for candidate in candidates if candidate == "chen-mo"]
            if not selected and candidates:
                selected = [candidates[0]]
            return json.dumps({"actor_ids": selected})
        enum = schema.get("properties", {}).get("choice", {}).get("enum", [])
        semantic = "No"
        if "Whose turn is next" in prompt:
            semantic = "chen-mo"
        elif "Classify the boundary" in prompt:
            semantic = "none"
        for option in enum:
            if re.search(
                rf"\({re.escape(str(option))}\)\s+{re.escape(semantic)}(?:\n|$)",
                prompt,
            ):
                return str(option)
        return str(enum[0])

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        messages = payload["messages"]
        prompt = str(messages[-1]["content"])
        self.calls.append(prompt)
        if "response_format" in payload:
            choice = self._choice(prompt, payload)
            content = choice if choice.startswith("{") else f'{{"choice":"{choice}"}}'
        elif "what does" in prompt.casefold() and "observe" in prompt.casefold():
            content = "The lighthouse mechanism bears fresh tool marks."
        elif "what action spec format" in prompt.casefold() or "output_type" in prompt:
            content = (
                '{"call_to_action":"Inspect the damaged mechanism.",'
                '"output_type":"free","options":[],"tag":"investigation"}'
            )
        elif "what actually results" in prompt.casefold():
            entity_changes = "[]"
            if self.entity_change == "create":
                entity_changes = (
                    '[{"operation":"create","entity_id":"harbor-guard",'
                    '"display_name":"Harbor Guard","identity":"A wary guard.",'
                    '"goal":"Secure the lighthouse.","location":"lighthouse"}]'
                )
            elif self.entity_change == "archive":
                entity_changes = '[{"operation":"archive","entity_id":"harbor-guard"}]'
            content = (
                '{"event_text":"Chen Mo finds a deliberately severed wire.",'
                '"boundary":"none","visibility":"participants",'
                '"observer_ids":[],"participant_ids":["chen-mo"],'
                f'"entity_changes":{entity_changes}}}'
            )
        else:
            content = "Chen Mo carefully inspects the lighthouse mechanism."
        return {
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 12,
                "completion_tokens": 6,
                "total_tokens": 18,
            },
        }

    async def stream(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[ModelStreamChunk]:
        del payload, timeout_seconds
        if False:
            yield ModelStreamChunk()
        raise AssertionError("simulation integration does not stream")

    def embed(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        del payload, timeout_seconds
        return {
            "data": [{"embedding": [1.0, 0.0, 0.0, 0.0]}],
            "usage": {"prompt_tokens": 1, "total_tokens": 1},
        }


def _settings(tmp_path: Path) -> EngineSettings:
    return EngineSettings(
        session_token="integration-token",
        projects_root=tmp_path,
        model_registry_path=tmp_path / "models.json",
    )


def test_real_application_chain_checkpoints_rebuilds_and_resumes(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    first_transport = ReplayGatewayTransport()
    with TestClient(
        create_app(_settings(tmp_path), model_transport=first_transport)
    ) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {
                    "mode": "step",
                    "max_steps": 3,
                    "checkpoint_every_steps": 1,
                },
            },
        )
        assert started.status_code == 201
        session_id = started.json()["session_id"]
        stepped = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/step",
            headers=AUTH,
        )
        assert stepped.status_code == 200
        assert stepped.json()["status"] == "paused"
        checkpoint_id = stepped.json()["checkpoint_id"]
        assert checkpoint_id.startswith("checkpoint-")

        trace = client.get(
            "/projects/fog-harbor/branches/main/simulation-trace",
            headers=AUTH,
        ).json()
        assert trace[0]["trace"]["stages"][-1]["stage_type"] == "commit"
        assert trace[0]["trace"]["model_calls"]
        assert all(call["component_ids"] for call in trace[0]["trace"]["model_calls"])

    second_transport = ReplayGatewayTransport()
    with TestClient(
        create_app(_settings(tmp_path), model_transport=second_transport)
    ) as client:
        restored = client.post(
            "/projects/fog-harbor/simulations/restore",
            headers=AUTH,
            json={"checkpoint_id": checkpoint_id},
        )
        assert restored.status_code == 200
        assert restored.json()["current_step"] == 1
        assert restored.json()["status"] == "paused"

        accepted = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/resume",
            headers=AUTH,
        )
        assert accepted.status_code == 202
        for _ in range(200):
            snapshot = client.get(
                f"/projects/fog-harbor/simulations/{session_id}",
                headers=AUTH,
            ).json()
            if (
                snapshot["status"] != "running"
                and snapshot["checkpoint_id"] != checkpoint_id
            ):
                break
            time.sleep(0.01)
        assert snapshot["status"] == "paused"
        assert snapshot["current_step"] == 2
        assert snapshot["checkpoint_id"] != checkpoint_id


def test_dynamic_npc_join_archive_and_checkpoint_restore(tmp_path: Path) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    with TestClient(
        create_app(
            _settings(tmp_path),
            model_transport=ReplayGatewayTransport(entity_change="create"),
        )
    ) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {
                    "mode": "step",
                    "max_steps": 3,
                    "allow_dynamic_entities": True,
                    "checkpoint_every_steps": 1,
                },
            },
        ).json()
        session_id = started["session_id"]
        created = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/step",
            headers=AUTH,
        ).json()
        checkpoint_id = created["checkpoint_id"]
        snapshot = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()
        assert "harbor-guard" in snapshot["active_entity_ids"]
        assert snapshot["dynamic_entities"][0]["active"] is True
        assert "harbor-guard" in snapshot["memory_snapshots"]

    with TestClient(
        create_app(
            _settings(tmp_path),
            model_transport=ReplayGatewayTransport(entity_change="archive"),
        )
    ) as client:
        restored = client.post(
            "/projects/fog-harbor/simulations/restore",
            headers=AUTH,
            json={"checkpoint_id": checkpoint_id},
        ).json()
        assert "harbor-guard" in restored["active_entity_ids"]
        archived = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/step",
            headers=AUTH,
        )
        assert archived.status_code == 200
        snapshot = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()
        assert "harbor-guard" not in snapshot["active_entity_ids"]
        assert snapshot["dynamic_entities"][0]["active"] is False
        assert "harbor-guard" in snapshot["actor_states"]


def test_game_master_selects_initial_roster_when_actors_are_not_pinned(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport()
    with TestClient(
        create_app(_settings(tmp_path), model_transport=transport)
    ) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 2},
            },
        ).json()
        session_id = started["session_id"]

        stepped = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/step",
            headers=AUTH,
        )
        snapshot = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()
        trace = client.get(
            "/projects/fog-harbor/branches/main/simulation-trace",
            headers=AUTH,
        ).json()[0]["trace"]

        assert stepped.status_code == 200
        assert snapshot["active_entity_ids"] == ["chen-mo"]
        assert any(
            "Select only the project characters" in prompt for prompt in transport.calls
        )
        assert any(
            "game-master:roster-selection" in call["component_ids"]
            for call in trace["model_calls"]
        )


def test_simulation_start_reports_unavailable_embedding_profile_as_model_error(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 2},
            },
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "model_configuration_error"
