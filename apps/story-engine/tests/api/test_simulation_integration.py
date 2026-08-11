import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.domain.simulation import TurnSessionStatus
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.gateway import ModelPartSink
from story_engine.models.registry import ProfileRegistry
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.persistence.simulation_log import SimulationLogStore
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.wiki.store import WikiStore

AUTH = {"Authorization": "Bearer integration-token"}


def _advance(
    client: TestClient,
    session_id: str,
    operation: str = "step",
    *,
    command_id: str | None = None,
    expected_state_hash: str | None = None,
):
    if expected_state_hash is None:
        expected_state_hash = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()["state_hash"]
    return client.post(
        f"/projects/fog-harbor/simulations/{session_id}/{operation}",
        headers=AUTH,
        json={
            "command_id": command_id or f"command:{uuid.uuid4().hex}",
            "expected_state_hash": expected_state_hash,
        },
    )


def _wait_for_projection(
    client: TestClient,
    session_id: str,
    *,
    kind: str,
    status: str,
    minimum_attempt_count: int = 0,
) -> dict[str, Any]:
    for _ in range(200):
        response = client.get(
            f"/projects/fog-harbor/simulations/{session_id}/projections",
            headers=AUTH,
        )
        assert response.status_code == 200, response.text
        task = next((item for item in response.json() if item["kind"] == kind), None)
        if (
            task is not None
            and task["status"] == status
            and task["attempt_count"] >= minimum_attempt_count
        ):
            return task
        time.sleep(0.01)
    raise AssertionError(f"{kind} projection did not reach {status}")


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
        invalid_wiki_attempts: int = 0,
    ) -> None:
        self.calls: list[str] = []
        self.entity_change = entity_change
        self.promote_npc = promote_npc
        self.boundary = boundary
        self.event_text = event_text
        self.fail_writer = fail_writer
        self.fail_editor = fail_editor
        self.fail_wiki_attempts = fail_wiki_attempts
        self.invalid_wiki_attempts = invalid_wiki_attempts

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
        actor_names = (
            properties.get("actor_names") if isinstance(properties, Mapping) else None
        )
        if isinstance(actor_names, Mapping):
            items = actor_names.get("items", {})
            candidates = items.get("enum", []) if isinstance(items, Mapping) else []
            selected = [
                candidate
                for candidate in candidates
                if candidate == "陈默"
                or (self.promote_npc and candidate == "Harbor Guard")
            ]
            if not selected and candidates:
                selected = [candidates[0]]
            return json.dumps({"actor_names": selected})
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
                    "observer_names": [],
                    "participant_names": [
                        "陈默",
                    ],
                    "entity_changes": entity_changes,
                }
            )
        if "promote" in properties:
            return json.dumps(
                {
                    "promote": self.promote_npc,
                    "proposed_goal": (
                        "Find who sabotaged the lighthouse"
                        if self.promote_npc
                        else None
                    ),
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
            semantic = "陈默"
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
        part_sink: ModelPartSink | None = None,
    ) -> Mapping[str, Any]:
        del timeout_seconds, first_content_timeout_seconds
        messages = payload["messages"]
        prompt = "\n".join(str(message["content"]) for message in messages)
        self.calls.append(prompt)
        if "Choose a contiguous range" in prompt:
            source_ids = re.findall(r'"source_id":\s*"([^"]+)"', prompt)
            content = json.dumps(
                {
                    "decision": "ready" if source_ids else "not_ready",
                    "source_ids": source_ids[:1],
                    "reason": "The first available scene boundary is coherent.",
                }
            )
        elif "Manuscript Context" in prompt:
            if self.fail_writer:
                raise RuntimeError("writer unavailable")
            content = f"# The Severed Wire\n\n{self.event_text}"
        elif "SOURCE_MANIFEST" in prompt:
            if self.fail_editor:
                raise RuntimeError("editor unavailable")
            content = json.dumps(
                {
                    "summary": "All concrete facts are supported.",
                    "issues": [],
                }
            )
        elif "WikiUpdateProposal only" in prompt:
            if self.fail_wiki_attempts > 0:
                self.fail_wiki_attempts -= 1
                raise RuntimeError("wiki unavailable")
            if self.invalid_wiki_attempts > 0:
                self.invalid_wiki_attempts -= 1
                content = json.dumps(
                    {
                        "updates": [
                            {
                                "page_ref": "index",
                                "content": "Invalid store-owned update.",
                                "source_refs": [0],
                            }
                        ]
                    }
                )
            else:
                source_refs = [
                    int(item) for item in re.findall(r'"source_ref":\s*(\d+)', prompt)
                ]
                assert source_refs
                page_ref = "state" if "Scope: World Wiki" in prompt else "beliefs"
                content = json.dumps(
                    {
                        "updates": [
                            {
                                "page_ref": page_ref,
                                "content": f"- {self.event_text}",
                                "source_refs": [source_refs[-1]],
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
                    '[{"display_name":"Harbor Guard",'
                    '"display_name":"Harbor Guard","identity":"A wary guard.",'
                    '"core_desire":"Keep the harbor safe.",'
                    '"location":"lighthouse"}]'
                )
            content = json.dumps(
                {
                    "event_text": self.event_text,
                    "boundary": self.boundary,
                    "visibility": "participants",
                    "observer_names": [],
                    "participant_names": [
                        "陈默",
                    ],
                    "entity_changes": json.loads(entity_changes),
                }
            )
        else:
            content = "Chen Mo carefully inspects the lighthouse mechanism."
        if part_sink is not None:
            part_sink("text", content)
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
                update={"model": f"test-provider/test-{profile.agent_type}"}
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
                },
            },
        )
        assert started.status_code == 201
        session_id = started.json()["session_id"]
        stepped = _advance(client, session_id)
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

        accepted = _advance(client, session_id, "resume")
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
                },
            },
        ).json()
        stepped = _advance(client, started["session_id"]).json()

        sources = client.get(
            "/projects/fog-harbor/branches/main/manuscript/sources",
            headers=AUTH,
        )
        assert sources.status_code == 200
        source = sources.json()[0]
        assert source["from_step"] == source["to_step"] == 0
        assert source["checkpoint_id"] == stepped["checkpoint_id"]

        generated = client.post(
            "/projects/fog-harbor/branches/main/manuscript/scenes",
            headers=AUTH,
            json={
                "source": {"mode": "scene", "source_id": source["source_id"]},
                "chapter_id": "chapter-001",
                "viewpoint_actor_id": "chen-mo",
            },
        )
        assert generated.status_code == 201, generated.text
        draft = generated.json()
        assert draft["branch_id"] == "main"
        assert draft["source"]["checkpoint_id"] == stepped["checkpoint_id"]
        assert draft["source"]["from_step"] == draft["source"]["to_step"] == 0
        assert draft["source"]["event_ids"]
        assert draft["source"]["memory_ids"]
        assert draft["source"]["wiki_version_id"] == "seed"
        assert draft["source"]["viewpoint_actor_id"] == "chen-mo"
        writer_prompt = next(
            prompt for prompt in transport.calls if "Manuscript Context" in prompt
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
                "control": {"mode": "step", "max_steps": 3, "max_scenes": 3},
                "output": {
                    "wiki_mode": "after_scene",
                    "manuscript_mode": "manual",
                },
            },
        ).json()
        first = _advance(client, started["session_id"]).json()
        _wait_for_projection(
            client,
            started["session_id"],
            kind="wiki",
            status="succeeded",
        )
        transport.event_text = "Chen Mo discovers a future hidden transmitter."
        _advance(client, started["session_id"])

        generated = client.post(
            "/projects/fog-harbor/branches/main/manuscript/scenes",
            headers=AUTH,
            json={
                "source": {
                    "mode": "manual",
                    "source_ids": [
                        f"source:main:{first['checkpoint_id']}:0:0",
                    ],
                },
                "chapter_id": "chapter-001",
                "viewpoint_actor_id": "chen-mo",
            },
        )

        assert generated.status_code == 201, generated.text
        draft = generated.json()
        assert draft["source"]["wiki_version_id"] == first["checkpoint_id"]
        writer_prompt = next(
            prompt
            for prompt in reversed(transport.calls)
            if "Manuscript Context" in prompt
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
        _advance(client, started["session_id"])

        with pytest.raises(RuntimeError, match="editor unavailable"):
            client.post(
                "/projects/fog-harbor/branches/main/manuscript/scenes",
                headers=AUTH,
                json={
                    "source": {"mode": "writer"},
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
        assert any(page["path"] == "world/rules.md" for page in initial.json()["pages"])
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
                "control": {"mode": "step", "max_steps": 3, "max_scenes": 3},
                "output": {
                    "wiki_mode": "after_scene",
                    "manuscript_mode": "after_scene",
                },
            },
        ).json()
        stepped = _advance(client, started["session_id"])
        assert stepped.status_code == 200, stepped.text
        checkpoint_id = stepped.json()["checkpoint_id"]
        _wait_for_projection(
            client,
            started["session_id"],
            kind="wiki",
            status="succeeded",
        )
        _wait_for_projection(
            client,
            started["session_id"],
            kind="manuscript",
            status="succeeded",
        )

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
            prompt for prompt in transport.calls if "WikiUpdateProposal only" in prompt
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
        assert scenes[0]["source"]["checkpoint_id"] == checkpoint_id


def test_wiki_projection_failure_does_not_pause_and_can_be_retried(
    tmp_path: Path,
) -> None:
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
                "control": {"mode": "step", "max_steps": 3, "max_scenes": 3},
                "output": {
                    "wiki_mode": "after_scene",
                    "manuscript_mode": "manual",
                },
            },
        ).json()
        session_id = started["session_id"]

        stepped = _advance(client, session_id)
        assert stepped.status_code == 200
        failed = _wait_for_projection(
            client,
            session_id,
            kind="wiki",
            status="failed",
        )
        assert "wiki unavailable" in failed["error_text"]
        transport.boundary = "none"
        continued = _advance(client, session_id)
        assert continued.status_code == 200, continued.text

    with TestClient(
        create_app(_settings(tmp_path), model_transport=transport)
    ) as client:
        recovered = _wait_for_projection(
            client,
            session_id,
            kind="wiki",
            status="failed",
        )
        assert "wiki unavailable" in recovered["error_text"]
        retried = client.post(
            (
                f"/projects/fog-harbor/simulations/{session_id}/projections/"
                f"{recovered['task_id']}/retry"
            ),
            headers=AUTH,
        )
        assert retried.status_code == 202, retried.text
        succeeded = _wait_for_projection(
            client,
            session_id,
            kind="wiki",
            status="succeeded",
        )
        assert succeeded["attempt_count"] == 2


def test_projection_rebuild_replays_reachable_wiki_tasks_in_checkpoint_order(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport(boundary="scene")
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
                "control": {"mode": "step", "max_steps": 3, "max_scenes": 3},
                "output": {
                    "wiki_mode": "after_scene",
                    "manuscript_mode": "manual",
                },
            },
        ).json()
        session_id = started["session_id"]

        first = _advance(client, session_id)
        assert first.status_code == 200, first.text
        _wait_for_projection(client, session_id, kind="wiki", status="succeeded")
        second = _advance(client, session_id)
        assert second.status_code == 200, second.text
        for _ in range(200):
            existing = client.get(
                f"/projects/fog-harbor/simulations/{session_id}/projections",
                headers=AUTH,
            ).json()
            wiki_tasks = [task for task in existing if task["kind"] == "wiki"]
            if len(wiki_tasks) == 2 and all(
                task["status"] == "succeeded" for task in wiki_tasks
            ):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("initial Wiki projections did not complete")

        rebuilt = client.post(
            f"/projects/fog-harbor/simulations/{session_id}/projections/rebuild",
            headers=AUTH,
            json={"kind": "wiki"},
        )
        assert rebuilt.status_code == 202, rebuilt.text
        assert len(rebuilt.json()) == 2

        for _ in range(200):
            tasks = client.get(
                f"/projects/fog-harbor/simulations/{session_id}/projections",
                headers=AUTH,
            ).json()
            wiki_tasks = [task for task in tasks if task["kind"] == "wiki"]
            if len(wiki_tasks) == 2 and all(
                task["status"] == "succeeded"
                and task["attempt_count"] >= 2
                for task in wiki_tasks
            ):
                break
            time.sleep(0.01)
        else:
            raise AssertionError("Wiki projection rebuild did not complete")

        assert [task["step"] for task in wiki_tasks] == [0, 1]


def test_repeated_wiki_protocol_errors_degrade_without_blocking_next_step(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport(
        boundary="scene",
        invalid_wiki_attempts=2,
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
                "control": {"mode": "step", "max_steps": 3, "max_scenes": 3},
                "output": {
                    "wiki_mode": "after_scene",
                    "manuscript_mode": "after_scene",
                },
            },
        ).json()
        session_id = started["session_id"]

        stepped = _advance(client, session_id)
        assert stepped.status_code == 200, stepped.text
        failed = _wait_for_projection(
            client,
            session_id,
            kind="wiki",
            status="failed",
        )
        assert "failed after 2 attempts" in failed["error_text"]

        wiki = client.get(
            "/projects/fog-harbor/branches/main/wiki",
            headers=AUTH,
        ).json()
        assert wiki["stale"] is True
        assert wiki["degraded"] is True
        assert "unknown page_ref 'index'" in wiki["degradation_reason"]

        scenes = client.get(
            "/projects/fog-harbor/branches/main/manuscript/scenes",
            headers=AUTH,
        ).json()
        assert scenes == []
        _wait_for_projection(
            client,
            session_id,
            kind="manuscript",
            status="failed",
        )
        manuscript = client.post(
            "/projects/fog-harbor/branches/main/manuscript/scenes",
            headers=AUTH,
            json={
                "source": {"mode": "writer"},
                "chapter_id": "chapter-001",
                "viewpoint_actor_id": None,
            },
        )
        assert manuscript.status_code == 409
        assert (
            "cannot generate manuscript from stale Wiki" in manuscript.json()["detail"]
        )

        continued = _advance(client, session_id)
        assert continued.status_code == 200, continued.text


def test_wiki_projection_retry_can_transition_to_degraded(tmp_path: Path) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport(
        boundary="scene",
        fail_wiki_attempts=1,
        invalid_wiki_attempts=2,
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
                "control": {"mode": "step", "max_steps": 3, "max_scenes": 3},
                "output": {
                    "wiki_mode": "after_scene",
                    "manuscript_mode": "manual",
                },
            },
        ).json()
        session_id = started["session_id"]

        stepped = _advance(client, session_id)
        assert stepped.status_code == 200, stepped.text
        failed = _wait_for_projection(
            client,
            session_id,
            kind="wiki",
            status="failed",
        )

        retried = client.post(
            (
                f"/projects/fog-harbor/simulations/{session_id}/projections/"
                f"{failed['task_id']}/retry"
            ),
            headers=AUTH,
        )
        assert retried.status_code == 202, retried.text
        _wait_for_projection(
            client,
            session_id,
            kind="wiki",
            status="failed",
            minimum_attempt_count=2,
        )

        wiki = client.get(
            "/projects/fog-harbor/branches/main/wiki",
            headers=AUTH,
        ).json()
        assert wiki["stale"] is True
        assert wiki["degraded"] is True

        continued = _advance(client, session_id)
        assert continued.status_code == 200, continued.text


def test_wiki_degradation_write_failure_remains_an_isolated_task_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())

    def fail_mark_degraded(
        self,
        checkpoint_id: str,
        step: int,
        reason: str,
    ) -> None:
        del self, checkpoint_id, step, reason
        raise OSError("wiki index is not writable")

    monkeypatch.setattr(WikiStore, "mark_degraded", fail_mark_degraded)
    transport = ReplayGatewayTransport(
        boundary="scene",
        invalid_wiki_attempts=2,
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
                "control": {"mode": "step", "max_steps": 3, "max_scenes": 3},
                "output": {
                    "wiki_mode": "after_scene",
                    "manuscript_mode": "manual",
                },
            },
        ).json()
        session_id = started["session_id"]

        stepped = _advance(client, session_id)
        assert stepped.status_code == 200, stepped.text
        failed = _wait_for_projection(
            client,
            session_id,
            kind="wiki",
            status="failed",
        )
        assert failed["error_text"] == "wiki index is not writable"
        continued = _advance(client, session_id)
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
        stepped = _advance(client, started["session_id"])

        assert stepped.status_code == 200
        _wait_for_projection(
            client,
            started["session_id"],
            kind="wiki",
            status="succeeded",
        )
        failed = _wait_for_projection(
            client,
            started["session_id"],
            kind="manuscript",
            status="failed",
        )
        assert "writer unavailable" in failed["error_text"]
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
        assert (
            client.get(
                "/projects/fog-harbor/branches/main/manuscript/scenes",
                headers=AUTH,
            ).json()
            == []
        )


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
        main_step = _advance(client, main["session_id"]).json()
        _wait_for_projection(
            client,
            main["session_id"],
            kind="wiki",
            status="succeeded",
        )
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
        advanced = _advance(client, alternate.json()["session_id"])
        assert advanced.status_code == 200, advanced.text
        _wait_for_projection(
            client,
            alternate.json()["session_id"],
            kind="wiki",
            status="succeeded",
        )

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
            "/projects/fog-harbor/branches/main/manuscript/sources", headers=AUTH
        ).json()
        alternate_sources = client.get(
            "/projects/fog-harbor/branches/alternate/manuscript/sources", headers=AUTH
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
    expected_actor_states: dict[str, object]
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
        stepped = _advance(client, session_id).json()
        checkpoint_id = stepped["checkpoint_id"]
        expected_snapshot = client.get(
            f"/projects/fog-harbor/simulations/{session_id}", headers=AUTH
        ).json()
        expected_actor_states = expected_snapshot["actor_states"]
        assert "memory_snapshots" not in expected_snapshot

    with TestClient(
        create_app(_settings(tmp_path), model_transport=ReplayGatewayTransport())
    ) as client:
        manifests = client.get("/projects/fog-harbor/simulations", headers=AUTH).json()
        restored = client.get(
            f"/projects/fog-harbor/simulations/{session_id}", headers=AUTH
        )

        assert any(item["session_id"] == session_id for item in manifests)
        assert restored.status_code == 200
        assert restored.json()["status"] == "paused"
        assert restored.json()["current_step"] == 1
        assert restored.json()["checkpoint_id"] == checkpoint_id
        assert restored.json()["actor_states"] == expected_actor_states
        assert "memory_snapshots" not in restored.json()
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
        terminal_step = _advance(client, terminal_id)
        assert terminal_step.json()["status"] == "terminated"

    with TestClient(
        create_app(_settings(tmp_path), model_transport=ReplayGatewayTransport())
    ) as client:
        archived = client.get(
            f"/projects/fog-harbor/simulations/{terminal_id}", headers=AUTH
        )
        assert archived.status_code == 200
        assert archived.json()["status"] == "terminated"


def test_checkpoint_is_authoritative_when_the_session_index_is_stale(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    session_id: str
    checkpoint_id: str
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
        stepped = _advance(client, session_id).json()
        checkpoint_id = stepped["checkpoint_id"]

    kernel = SimulationCommitKernel(tmp_path / "fog-harbor")
    stale_index = kernel.sessions.load(session_id).model_copy(
        update={
            "status": TurnSessionStatus.FAILED,
            "current_step": 99,
        }
    )
    kernel.sessions.save(stale_index)

    with TestClient(
        create_app(_settings(tmp_path), model_transport=ReplayGatewayTransport())
    ) as client:
        restored = client.get(
            f"/projects/fog-harbor/simulations/{session_id}", headers=AUTH
        )

    assert restored.status_code == 200
    assert restored.json()["checkpoint_id"] == checkpoint_id
    assert restored.json()["status"] == "paused"
    assert restored.json()["current_step"] == 1


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
        stepped = _advance(client, started["session_id"]).json()
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
        assert "harbor-guard" not in snapshot["actor_states"]
        assert snapshot["pending_scene_events"]
        assert stepped["promotion_decisions"] == []


def test_existing_npc_is_reused_across_consecutive_steps(tmp_path: Path) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    transport = ReplayGatewayTransport(entity_change="create")
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
                "control": {"mode": "step", "max_steps": 4},
            },
        ).json()
        session_id = started["session_id"]

        first = _advance(client, session_id)
        second = _advance(client, session_id)
        snapshot = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()

        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert len(first.json()["resolved_turn"]["effects"]) == 1
        assert second.json()["resolved_turn"]["effects"] == []
        assert (
            "harbor-guard"
            in second.json()["resolved_turn"]["events"][0]["participant_ids"]
        )
        assert (
            sum(
                character["id"] == "harbor-guard"
                for character in snapshot["characters"]
            )
            == 1
        )
        assert any(
            "Existing characters:" in prompt
            and "- Harbor Guard, ordinary NPC" in prompt
            and "harbor-guard" not in prompt
            for prompt in transport.calls
        )


def test_checkpoint_restore_reuses_existing_npc_in_next_resolution(
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
                "control": {"mode": "step", "max_steps": 4},
            },
        ).json()
        session_id = started["session_id"]
        created = _advance(client, session_id)
        assert created.status_code == 200, created.text
        checkpoint_id = created.json()["checkpoint_id"]

    with TestClient(
        create_app(
            _settings(tmp_path),
            model_transport=ReplayGatewayTransport(entity_change="create"),
        )
    ) as client:
        restored = client.post(
            "/projects/fog-harbor/simulations/restore",
            headers=AUTH,
            json={"checkpoint_id": checkpoint_id},
        )
        assert restored.status_code == 200, restored.text

        next_step = _advance(client, session_id)
        snapshot = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()

        assert next_step.status_code == 200, next_step.text
        assert next_step.json()["resolved_turn"]["effects"] == []
        assert (
            sum(
                character["id"] == "harbor-guard"
                for character in snapshot["characters"]
            )
            == 1
        )


def test_dynamic_npc_remains_npc_at_scene_boundary_and_after_restore(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    with TestClient(
        create_app(
            _settings(tmp_path),
            model_transport=ReplayGatewayTransport(
                entity_change="create",
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
                "output": {"wiki_mode": "manual"},
                "control": {
                    "mode": "step",
                    "max_steps": 3,
                    "max_scenes": 2,
                },
            },
        ).json()
        session_id = started["session_id"]
        created = _advance(client, session_id).json()
        checkpoint_id = created["checkpoint_id"]
        snapshot = client.get(
            f"/projects/fog-harbor/simulations/{session_id}",
            headers=AUTH,
        ).json()
        guard = next(
            item for item in snapshot["characters"] if item["id"] == "harbor-guard"
        )
        assert guard["type"] == "npc"
        assert guard["current_goal"] is None
        assert "harbor-guard" not in snapshot["roster_actor_ids"]
        assert "harbor-guard" not in snapshot["actor_states"]
        assert created["promotion_decisions"] == []
        assert all(
            not page.path.startswith("characters/harbor-guard/")
            for page in WikiStore(
                tmp_path / "fog-harbor", "main"
            ).list_pages()
        )

        reused = _advance(client, session_id)
        assert reused.status_code == 200, reused.text
        assert reused.json()["resolved_turn"]["effects"] == []
        assert reused.json()["promotion_decisions"] == []

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
        assert "harbor-guard" not in restored["roster_actor_ids"]
        restored_guard = next(
            item for item in restored["characters"] if item["id"] == "harbor-guard"
        )
        assert restored_guard["type"] == "npc"
        assert "harbor-guard" not in restored["actor_states"]


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

        stepped = _advance(client, session_id)
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
