import json
import re
import time
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.registry import ProfileRegistry
from story_engine.persistence.simulation_log import SimulationLogStore
from story_engine.submission.service import SubmissionService, fog_harbor_submission

AUTH = {"Authorization": "Bearer integration-token"}


class ReplayGatewayTransport:
    """Prompt-addressed replay that still exercises ModelGateway validation."""

    def __init__(
        self,
        *,
        entity_change: str | None = None,
        promote_npc: bool = False,
        boundary: str = "none",
        event_text: str = "Chen Mo finds a deliberately severed wire.",
        fail_writer: bool = False,
        fail_editor: bool = False,
        fail_wiki_attempts: int = 0,
    ) -> None:
        self.calls: list[str] = []
        self.entity_change = entity_change
        self.promote_npc = promote_npc
        self.boundary = boundary
        self.event_text = event_text
        self.fail_writer = fail_writer
        self.fail_editor = fail_editor
        self.fail_wiki_attempts = fail_wiki_attempts

    def _choice(self, prompt: str, payload: Mapping[str, Any]) -> str:
        response_format = payload.get("response_format")
        schema = (
            response_format.get("json_schema", {}).get("schema", {})
            if isinstance(response_format, Mapping)
            else {}
        )
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            properties = {}
        actor_ids = (
            properties.get("actor_ids") if isinstance(properties, Mapping) else None
        )
        if isinstance(actor_ids, Mapping):
            items = actor_ids.get("items", {})
            candidates = items.get("enum", []) if isinstance(items, Mapping) else []
            selected = [
                candidate
                for candidate in candidates
                if candidate == "chen-mo"
                or (self.promote_npc and candidate == "harbor-guard")
            ]
            if not selected and candidates:
                selected = [candidates[0]]
            return json.dumps({"actor_ids": selected})
        if "output_type" in properties:
            return json.dumps(
                {
                    "call_to_action": "Inspect the damaged mechanism.",
                    "output_type": "free",
                    "options": [],
                    "tag": "investigation",
                }
            )
        if "event_text" in properties:
            entity_changes = []
            if self.entity_change == "create":
                entity_changes = [
                    {
                        "operation": "create_npc",
                        "entity_id": "harbor-guard",
                        "display_name": "Harbor Guard",
                        "identity": "A wary guard.",
                        "core_desire": "Keep the harbor safe.",
                        "location": "lighthouse",
                    }
                ]
            return json.dumps(
                {
                    "event_text": self.event_text,
                    "boundary": self.boundary,
                    "visibility": "participants",
                    "observer_ids": [],
                    "participant_ids": [
                        "chen-mo",
                        *(["harbor-guard"] if self.entity_change == "create" else []),
                    ],
                    "entity_changes": entity_changes,
                }
            )
        if "promote" in properties:
            event_ids = re.findall(r'"event_id":\s*"([^"]+)"', prompt)
            return json.dumps(
                {
                    "character_id": "harbor-guard",
                    "promote": self.promote_npc,
                    "proposed_goal": (
                        "Find who sabotaged the lighthouse"
                        if self.promote_npc
                        else None
                    ),
                    "evidence_event_ids": event_ids[-1:] if self.promote_npc else [],
                    "reason": (
                        "The guard independently pursues the saboteur."
                        if self.promote_npc
                        else "The guard remains an ordinary participant."
                    ),
                }
            )
        enum = properties.get("choice", {}).get("enum", [])
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
        first_content_timeout_seconds: float | None = None,
    ) -> Mapping[str, Any]:
        del timeout_seconds, first_content_timeout_seconds
        messages = payload["messages"]
        prompt = "\n".join(str(message["content"]) for message in messages)
        self.calls.append(prompt)
        if "Writer Context" in prompt:
            if self.fail_writer:
                raise RuntimeError("writer unavailable")
            content = json.dumps(
                {
                    "title": "The Severed Wire",
                    "body": self.event_text,
                }
            )
        elif "Editor Context" in prompt:
            if self.fail_editor:
                raise RuntimeError("editor unavailable")
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
        elif "WikiPatch objects only" in prompt:
            if self.fail_wiki_attempts > 0:
                self.fail_wiki_attempts -= 1
                raise RuntimeError("wiki unavailable")
            source_ids = re.findall(r'"source_id":\s*"([^"]+)"', prompt)
            if "Scope: World Wiki" in prompt:
                source_id = next(
                    item for item in source_ids if item.startswith("event:")
                )
                path = "world/state.md"
            else:
                source_id = next(
                    item
                    for item in reversed(source_ids)
                    if item.startswith("observation:")
                    or item.startswith("event-observation:")
                )
                subject = re.search(r"Scope: Character Wiki: ([a-z0-9-]+)", prompt)
                assert subject is not None
                path = f"characters/{subject.group(1)}/beliefs.md"
            content = json.dumps(
                {
                    "patches": [
                        {
                            "path": path,
                            "section": None,
                            "operation": "append_history",
                            "content": f"## Step Update\n\n- {self.event_text}",
                            "source_ids": [source_id],
                            "confidence": 1.0,
                        }
                    ]
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
                    '[{"operation":"create_npc","entity_id":"harbor-guard",'
                    '"display_name":"Harbor Guard","identity":"A wary guard.",'
                    '"core_desire":"Keep the harbor safe.",'
                    '"location":"lighthouse"}]'
                )
            content = (
                json.dumps(
                    {
                        "event_text": self.event_text,
                        "boundary": self.boundary,
                        "visibility": "participants",
                        "observer_ids": [],
                        "participant_ids": [
                            "chen-mo",
                            *(
                                ["harbor-guard"]
                                if self.entity_change == "create"
                                else []
                            ),
                        ],
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

def _settings(tmp_path: Path) -> EngineSettings:
    settings = EngineSettings(
        session_token="integration-token",
        projects_root=tmp_path,
        model_registry_path=tmp_path / "models.json",
    )
    registry = ProfileRegistry(settings.model_registry_path)
    for profile in registry.load().profiles:
        registry.upsert_profile(
            profile.model_copy(
                update={
                    "model": f"test-provider/test-{profile.agent_type}"
                }
            )
        )
    return settings


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
                "actor_ids": ["chen-mo", "lin-lan"],
                "content_locale": "en-US",
                    "control": {
                        "mode": "step",
                        "max_steps": 3,
                        "max_scenes": 2,
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
                    "max_scenes": 2,
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
        assert draft["source_wiki_branch_id"] == "main"
        assert draft["source_wiki_version_id"] == "seed"
        assert draft["viewpoint_actor_id"] == "chen-mo"
        writer_prompt = next(
                prompt for prompt in transport.calls if "Writer Context" in prompt
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
        scene_text = (
            tmp_path
            / "fog-harbor/.story-engine/manuscript/main/scenes"
            / f"{draft['id']}.md"
        ).read_text(encoding="utf-8")
        assert "# The Severed Wire" not in scene_text

        exported = client.get(
            "/projects/fog-harbor/branches/main/manuscript/export",
            headers=AUTH,
        )
        assert exported.status_code == 200
        assert exported.json()["branch_id"] == "main"
        assert "## chapter-001" in exported.json()["markdown"]
        assert "### The Severed Wire" in exported.json()["markdown"]


def test_early_checkpoint_writer_uses_its_historical_wiki_without_future_facts(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport(
        boundary="scene",
        event_text="Chen Mo records the first damaged cable.",
    )
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
                "control": {"mode": "step", "max_steps": 3},
                "output": {
                    "wiki_mode": "after_scene",
                    "manuscript_mode": "manual",
                },
            },
        ).json()
        first = client.post(
            f"/projects/fog-harbor/simulations/{started['session_id']}/step",
            headers=AUTH,
        ).json()
        transport.event_text = "Chen Mo discovers a future hidden transmitter."
        client.post(
            f"/projects/fog-harbor/simulations/{started['session_id']}/step",
            headers=AUTH,
        )

        generated = client.post(
            "/projects/fog-harbor/branches/main/manuscript/scenes/generate",
            headers=AUTH,
            json={
                "checkpoint_id": first["checkpoint_id"],
                "from_step": 0,
                "to_step": 0,
                "chapter_id": "chapter-001",
                "viewpoint_actor_id": "chen-mo",
            },
        )

        assert generated.status_code == 201, generated.text
        draft = generated.json()
        assert draft["source_wiki_version_id"] == first["checkpoint_id"]
        writer_prompt = next(
            prompt
            for prompt in reversed(transport.calls)
            if "Writer Context" in prompt
        )
        assert "first damaged cable" in writer_prompt
        assert "future hidden transmitter" not in writer_prompt


def test_editor_failure_keeps_the_writer_draft_on_disk(tmp_path: Path) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport(fail_editor=True)
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
                "control": {"mode": "step", "max_steps": 2},
            },
        ).json()
        stepped = client.post(
            f"/projects/fog-harbor/simulations/{started['session_id']}/step",
            headers=AUTH,
        ).json()

        with pytest.raises(RuntimeError, match="editor unavailable"):
            client.post(
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

        drafts = client.get(
            "/projects/fog-harbor/branches/main/manuscript/scenes",
            headers=AUTH,
        ).json()
        assert len(drafts) == 1
        assert drafts[0]["status"] == "draft"
        assert drafts[0]["body"] == transport.event_text


def test_submission_wiki_and_scene_boundary_outputs_form_a_closed_loop(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport(boundary="scene")
    with TestClient(
        create_app(_settings(tmp_path), model_transport=transport)
    ) as client:
        initial = client.get(
            "/projects/fog-harbor/branches/main/wiki",
            headers=AUTH,
        )
        assert initial.status_code == 200
        assert initial.json()["checkpoint_id"] is None
        assert any(
            page["path"] == "world/rules.md"
            for page in initial.json()["pages"]
        )
        character_page = client.get(
            "/projects/fog-harbor/branches/main/wiki/page",
            params={"path": "characters/chen-mo/self.md"},
            headers=AUTH,
        )
        assert "陈默" in character_page.json()["content"]

        started = client.post(
            "/projects/fog-harbor/simulations",
            headers=AUTH,
            json={
                "premise_text": "The lighthouse suddenly goes dark.",
                "actor_ids": ["chen-mo", "lin-lan"],
                "content_locale": "en-US",
                "control": {"mode": "step", "max_steps": 3},
                "output": {
                    "wiki_mode": "after_scene",
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

        wiki = client.get(
            "/projects/fog-harbor/branches/main/wiki",
            headers=AUTH,
        ).json()
        world = client.get(
            "/projects/fog-harbor/branches/main/wiki/page",
            params={"path": "world/state.md"},
            headers=AUTH,
        ).json()
        scenes = client.get(
            "/projects/fog-harbor/branches/main/manuscript/scenes",
            headers=AUTH,
        ).json()

        assert wiki["checkpoint_id"] == checkpoint_id
        assert "severed wire" in world["content"]
        assert any(item.startswith("event:") for item in world["source_ids"])
        raw_store = SimulationLogStore(tmp_path / "fog-harbor")
        event_source = next(
            item for item in world["source_ids"] if item.startswith("event:")
        )
        assert raw_store.find_source("main", event_source).suffix == ".md"
        assert world["updated_at_step"] == 0
        chen_page = client.get(
            "/projects/fog-harbor/branches/main/wiki/page",
            params={"path": "characters/chen-mo/beliefs.md"},
            headers=AUTH,
        ).json()
        chen_beliefs = chen_page["content"]
        lin_beliefs = client.get(
            "/projects/fog-harbor/branches/main/wiki/page",
            params={"path": "characters/lin-lan/beliefs.md"},
            headers=AUTH,
        ).json()["content"]
        assert "severed wire" in chen_beliefs
        assert "severed wire" in lin_beliefs
        observation_source = next(
            item
            for item in chen_page["source_ids"]
            if item.startswith(("observation:", "event-observation:"))
        )
        assert raw_store.find_source("main", observation_source).suffix == ".md"
        wiki_prompts = [
            prompt
            for prompt in transport.calls
            if "WikiPatch objects only" in prompt
        ]
        chen_prompt = next(
            prompt for prompt in wiki_prompts if "Character Wiki: chen-mo" in prompt
        )
        lin_prompt = next(
            prompt for prompt in wiki_prompts if "Character Wiki: lin-lan" in prompt
        )
        assert "secret:lin-unfiled-duty-roster" not in chen_prompt
        assert "secret:chen-father-disappearance" not in lin_prompt
        assert len(scenes) == 1
        assert scenes[0]["branch_id"] == "main"
        assert scenes[0]["source_checkpoint_id"] == checkpoint_id


def test_wiki_failure_pauses_blocks_and_can_be_retried(tmp_path: Path) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport(boundary="scene", fail_wiki_attempts=1)
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
                "control": {"mode": "step", "max_steps": 3},
                "output": {
                    "wiki_mode": "after_scene",
                    "manuscript_mode": "manual",
                },
            },
        ).json()
        session_id = started["session_id"]

        stepped = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/step",
            headers=AUTH,
        )
        assert stepped.status_code == 200
        failed = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()
        assert failed["status"] == "paused"
        assert failed["maintenance_status"] == "failed"
        assert "wiki unavailable" in failed["maintenance_error_text"]

    with TestClient(
        create_app(_settings(tmp_path), model_transport=transport)
    ) as client:
        recovered = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["status"] == "paused"
        assert recovered.json()["maintenance_status"] == "failed"
        assert "wiki unavailable" in recovered.json()["maintenance_error_text"]

        blocked = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/step",
            headers=AUTH,
        )
        assert blocked.status_code == 409
        assert "retry maintenance" in blocked.json()["detail"]

        retried = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/maintenance/retry",
            headers=AUTH,
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["maintenance_status"] == "succeeded"
        assert retried.json()["status"] == "paused"

    with TestClient(
        create_app(_settings(tmp_path), model_transport=transport)
    ) as client:
        recovered = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        )
        assert recovered.status_code == 200, recovered.text
        assert recovered.json()["status"] == "paused"
        assert recovered.json()["maintenance_status"] == "succeeded"

        continued = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/step",
            headers=AUTH,
        )
        assert continued.status_code == 200, continued.text


def test_writer_failure_keeps_committed_history_and_wiki(
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
                    "wiki_mode": "after_scene",
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
        world = client.get(
            "/projects/fog-harbor/branches/main/wiki/page",
            params={"path": "world/state.md"},
            headers=AUTH,
        ).json()
        assert "severed wire" in world["content"]
        assert client.get(
            "/projects/fog-harbor/branches/main/manuscript/scenes",
            headers=AUTH,
        ).json() == []
        failures = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (
                tmp_path / "fog-harbor/.story-engine/runtime/output-failures"
            ).glob("*.md")
        )
        assert "writer unavailable" in failures


def test_wiki_and_narrative_sources_are_isolated_by_branch(
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
            "/projects/fog-harbor/branches/main/wiki/page",
            params={"path": "world/state.md"},
            headers=AUTH,
        ).json()
        alternate_world = client.get(
            "/projects/fog-harbor/branches/alternate/wiki/page",
            params={"path": "world/state.md"},
            headers=AUTH,
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


def test_new_npc_remains_a_non_agent_before_the_scene_boundary(
    tmp_path: Path,
) -> None:
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
                "control": {"mode": "step", "max_steps": 3},
            },
        ).json()
        stepped = client.post(
            f"/projects/fog-harbor/simulations/{started['session_id']}/step",
            headers=AUTH,
        ).json()
        snapshot = client.get(
            f"/projects/fog-harbor/simulations/{started['session_id']}",
            headers=AUTH,
        ).json()

        guard = next(
            item for item in snapshot["characters"] if item["id"] == "harbor-guard"
        )
        assert guard["type"] == "npc"
        assert guard["current_goal"] is None
        assert "harbor-guard" not in snapshot["roster_actor_ids"]
        assert "harbor-guard" not in snapshot["memory_snapshots"]
        assert snapshot["pending_scene_events"]
        assert stepped["promotion_decisions"] == []


def test_npc_is_automatically_promoted_at_scene_boundary_and_restored(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    with TestClient(
        create_app(
            _settings(tmp_path),
            model_transport=ReplayGatewayTransport(
                entity_change="create",
                promote_npc=True,
                boundary="scene",
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
                "control": {
                    "mode": "step",
                    "max_steps": 3,
                    "max_scenes": 2,
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
        guard = next(
            item for item in snapshot["characters"] if item["id"] == "harbor-guard"
        )
        assert guard["type"] == "active"
        assert guard["current_goal"] == "Find who sabotaged the lighthouse"
        assert "harbor-guard" in snapshot["roster_actor_ids"]
        assert "harbor-guard" in snapshot["memory_snapshots"]
        assert created["promotion_decisions"][0]["promote"] is True

    with TestClient(
        create_app(_settings(tmp_path), model_transport=ReplayGatewayTransport())
    ) as client:
        restored_response = client.post(
            "/projects/fog-harbor/simulations/restore",
            headers=AUTH,
            json={"checkpoint_id": checkpoint_id},
        )
        assert restored_response.status_code == 200, restored_response.text
        restored = restored_response.json()
        assert "harbor-guard" in restored["roster_actor_ids"]
        restored_guard = next(
            item for item in restored["characters"] if item["id"] == "harbor-guard"
        )
        assert restored_guard["type"] == "active"
        assert "harbor-guard" in restored["actor_states"]


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
        assert snapshot["roster_actor_ids"] == ["chen-mo"]
        assert any(
            "Select the opening scene roster" in prompt for prompt in transport.calls
        )
        assert any(
            "game-master:roster-selection" in call["component_ids"]
            for call in trace["model_calls"]
        )


def test_simulation_start_needs_no_embedding_model_or_bridge(
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

        assert response.status_code == 201
