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

    def __init__(
        self,
        *,
        entity_change: str | None = None,
        boundary: str = "none",
        event_text: str = "Chen Mo finds a deliberately severed wire.",
        fail_writer: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.entity_change = entity_change
        self.boundary = boundary
        self.event_text = event_text
        self.fail_writer = fail_writer

    def _choice(self, prompt: str, payload: Mapping[str, Any]) -> str:
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
            semantic = self.boundary
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
        if "Story Engine Writer" in prompt:
            if self.fail_writer:
                raise RuntimeError("writer unavailable")
            content = json.dumps(
                {
                    "title": "The Severed Wire",
                    "body": self.event_text,
                }
            )
        elif "manuscript Editor" in prompt:
            content = json.dumps(
                {
                    "review": {
                        "mode": "manuscript_review",
                        "passed": True,
                        "summary": "All concrete facts are supported.",
                        "issues": [],
                    },
                    "unsupported_facts": [],
                }
            )
        elif "response_format" in payload:
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
                json.dumps(
                    {
                        "event_text": self.event_text,
                        "boundary": self.boundary,
                        "visibility": "participants",
                        "observer_ids": [],
                        "participant_ids": ["chen-mo"],
                        "entity_changes": json.loads(entity_changes),
                    }
                )
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


def test_simulation_scene_generates_traceable_branch_manuscript(
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
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {
                    "mode": "step",
                    "max_steps": 3,
                    "checkpoint_every_steps": 1,
                },
            },
        ).json()
        stepped = client.post(
            f"/projects/fog-harbor/simulations/{started['session_id']}/step",
            headers=AUTH,
        ).json()

        sources = client.get(
            "/projects/fog-harbor/branches/main/narrative-sources",
            headers=AUTH,
        )
        assert sources.status_code == 200
        source = sources.json()[0]
        assert source["from_step"] == source["to_step"] == 0
        assert source["checkpoint_id"] == stepped["checkpoint_id"]

        generated = client.post(
            "/projects/fog-harbor/branches/main/manuscript/scenes/generate",
            headers=AUTH,
            json={
                "checkpoint_id": stepped["checkpoint_id"],
                "from_step": 0,
                "to_step": 0,
                "chapter_id": "chapter-001",
                "viewpoint_actor_id": "chen-mo",
            },
        )
        assert generated.status_code == 201, generated.text
        draft = generated.json()
        assert draft["branch_id"] == "main"
        assert draft["source_checkpoint_id"] == stepped["checkpoint_id"]
        assert draft["source_from_step"] == draft["source_to_step"] == 0
        assert draft["source_event_ids"]
        assert draft["source_memory_ids"]
        assert draft["viewpoint_actor_id"] == "chen-mo"
        writer_prompt = next(
            prompt for prompt in transport.calls if "Story Engine Writer" in prompt
        )
        assert "secret:lin-unfiled-duty-roster" not in writer_prompt

        saved = client.put(
            f"/projects/fog-harbor/branches/main/manuscript/scenes/{draft['id']}",
            headers=AUTH,
            json={
                "title": draft["title"],
                "body": draft["body"],
                "expected_revision": draft["revision"],
                "expected_scene_version": draft["base_scene_version"],
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["status"] == "saved"

        exported = client.get(
            "/projects/fog-harbor/branches/main/manuscript/export",
            headers=AUTH,
        )
        assert exported.status_code == 200
        assert exported.json()["branch_id"] == "main"
        assert "## The Severed Wire" in exported.json()["markdown"]


def test_submission_world_bible_and_scene_boundary_outputs_form_a_closed_loop(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport(boundary="scene")
    with TestClient(
        create_app(_settings(tmp_path), model_transport=transport)
    ) as client:
        initial = client.get(
            "/projects/fog-harbor/branches/main/world-bible",
            headers=AUTH,
        )
        assert initial.status_code == 200
        assert initial.json()["checkpoint_id"] == "seed:fog-harbor"
        assert initial.json()["rules"]
        assert initial.json()["established_facts"]
        characters_path = (
            tmp_path
            / "fog-harbor/.story-engine/projections/main/characters.md"
        )
        assert "陈默" in characters_path.read_text(encoding="utf-8")

        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
                "output": {
                    "world_projection_mode": "after_scene",
                    "manuscript_mode": "after_scene",
                },
            },
        ).json()
        stepped = client.post(
            f"/projects/fog-harbor/simulations/{started['session_id']}/step",
            headers=AUTH,
        )
        assert stepped.status_code == 200, stepped.text
        checkpoint_id = stepped.json()["checkpoint_id"]

        world = client.get(
            "/projects/fog-harbor/branches/main/world-bible",
            headers=AUTH,
        ).json()
        scenes = client.get(
            "/projects/fog-harbor/branches/main/manuscript/scenes",
            headers=AUTH,
        ).json()

        assert world["checkpoint_id"] == checkpoint_id
        generated_fact = next(
            entry
            for entry in world["established_facts"]
            if "severed wire" in entry["content_text"]
        )
        assert generated_fact["source_event_ids"]
        assert generated_fact["last_updated_step"] == 0
        assert len(scenes) == 1
        assert scenes[0]["branch_id"] == "main"
        assert scenes[0]["source_checkpoint_id"] == checkpoint_id


def test_writer_failure_keeps_committed_history_and_world_projection(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    with TestClient(
        create_app(
            _settings(tmp_path),
            model_transport=ReplayGatewayTransport(
                boundary="scene",
                fail_writer=True,
            ),
        )
    ) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
                "output": {
                    "world_projection_mode": "after_scene",
                    "manuscript_mode": "after_scene",
                },
            },
        ).json()
        stepped = client.post(
            f"/projects/fog-harbor/simulations/{started['session_id']}/step",
            headers=AUTH,
        )

        assert stepped.status_code == 200
        assert client.get(
            "/projects/fog-harbor/branches/main/simulation-trace",
            headers=AUTH,
        ).json()
        assert any(
            "severed wire" in entry["content_text"]
            for entry in client.get(
                "/projects/fog-harbor/branches/main/world-bible",
                headers=AUTH,
            ).json()["established_facts"]
        )
        assert client.get(
            "/projects/fog-harbor/branches/main/manuscript/scenes",
            headers=AUTH,
        ).json() == []
        failures = (
            tmp_path
            / "fog-harbor/.story-engine/runtime/output-failures.jsonl"
        ).read_text(encoding="utf-8")
        assert "writer unavailable" in failures


def test_world_bible_and_narrative_sources_are_isolated_by_branch(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport(
        boundary="scene",
        event_text="The main branch reveals a brass signal key.",
    )
    with TestClient(
        create_app(_settings(tmp_path), model_transport=transport)
    ) as client:
        main = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "Search the lighthouse.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 1},
            },
        ).json()
        main_step = client.post(
            f"/projects/fog-harbor/simulations/{main['session_id']}/step",
            headers=AUTH,
        ).json()
        forked = client.post(
            "/projects/fog-harbor/branches",
            headers=AUTH,
            json={
                "branch_id": "alternate",
                "source_checkpoint_id": main_step["checkpoint_id"],
                "parent_branch_id": "main",
                "content_locale": "en-US",
            },
        )
        assert forked.status_code == 201, forked.text

        transport.event_text = "The alternate branch opens a flooded tunnel."
        alternate = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "branch_id": "alternate",
                "premise_text": "Follow the forked possibility.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
            },
        )
        assert alternate.status_code == 201, alternate.text
        advanced = client.post(
            f"/projects/fog-harbor/simulations/{alternate.json()['session_id']}/step",
            headers=AUTH,
        )
        assert advanced.status_code == 200, advanced.text

        main_world = client.get(
            "/projects/fog-harbor/branches/main/world-bible", headers=AUTH
        ).json()
        alternate_world = client.get(
            "/projects/fog-harbor/branches/alternate/world-bible", headers=AUTH
        ).json()
        main_sources = client.get(
            "/projects/fog-harbor/branches/main/narrative-sources", headers=AUTH
        ).json()
        alternate_sources = client.get(
            "/projects/fog-harbor/branches/alternate/narrative-sources", headers=AUTH
        ).json()

        assert "flooded tunnel" not in json.dumps(main_world)
        assert "brass signal key" in json.dumps(main_world)
        assert "flooded tunnel" in json.dumps(alternate_world)
        assert "flooded tunnel" not in json.dumps(main_sources)
        assert "flooded tunnel" in json.dumps(alternate_sources)


def test_session_manifest_restores_on_get_and_keeps_terminal_history(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    session_id: str
    checkpoint_id: str
    expected_memories: dict[str, object]
    with TestClient(
        create_app(_settings(tmp_path), model_transport=ReplayGatewayTransport())
    ) as client:
        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
            },
        ).json()
        session_id = started["session_id"]
        stepped = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/step",
            headers=AUTH,
        ).json()
        checkpoint_id = stepped["checkpoint_id"]
        expected_memories = client.get(
            f"/projects/fog-harbor/simulations/{session_id}", headers=AUTH
        ).json()["memory_snapshots"]

    with TestClient(
        create_app(_settings(tmp_path), model_transport=ReplayGatewayTransport())
    ) as client:
        manifests = client.get(
            "/projects/fog-harbor/simulations", headers=AUTH
        ).json()
        restored = client.get(
            f"/projects/fog-harbor/simulations/{session_id}", headers=AUTH
        )

        assert any(item["session_id"] == session_id for item in manifests)
        assert restored.status_code == 200
        assert restored.json()["status"] == "paused"
        assert restored.json()["current_step"] == 1
        assert restored.json()["checkpoint_id"] == checkpoint_id
        restored_memories = restored.json()["memory_snapshots"]
        assert restored_memories.keys() == expected_memories.keys()
        assert {
            owner: (
                memory["record_count"],
                json.loads(memory["state"]["memory_bank"])["text"],
            )
            for owner, memory in restored_memories.items()
        } == {
            owner: (
                memory["record_count"],
                json.loads(memory["state"]["memory_bank"])["text"],
            )
            for owner, memory in expected_memories.items()
        }
        assert restored.json()["restoration_notice_text"]

        terminal = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "branch_id": "terminal-history",
                "premise_text": "Archive this short run.",
                "actor_ids": ["chen-mo"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 1},
            },
        )
        assert terminal.status_code == 201
        terminal_id = terminal.json()["session_id"]
        terminal_step = client.post(
            f"/projects/fog-harbor/simulations/{terminal_id}/step", headers=AUTH
        )
        assert terminal_step.json()["status"] == "terminated"

    with TestClient(
        create_app(_settings(tmp_path), model_transport=ReplayGatewayTransport())
    ) as client:
        archived = client.get(
            f"/projects/fog-harbor/simulations/{terminal_id}", headers=AUTH
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "terminated"


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
