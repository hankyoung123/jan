import json
from collections.abc import AsyncIterator, Mapping
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from story_engine.concordia_adapter.adapter import ConcordiaStoryAdapter
from story_engine.concordia_adapter.language_model import JanGatewayLanguageModel
from story_engine.domain.models import CharacterIntent, StateChange
from story_engine.evolution.context import CharacterContextAssembler
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.gateway import ModelGateway
from story_engine.models.registry import ProfileRegistry
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.project_store import ProjectStore


class QueuedTransport:
    def __init__(self, *contents: str) -> None:
        self.contents = list(contents)
        self.calls: list[Mapping[str, Any]] = []
        self.timeouts: list[int] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        self.calls.append(payload)
        self.timeouts.append(timeout_seconds)
        return {
            "choices": [
                {
                    "message": {"content": self.contents.pop(0)},
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
        raise AssertionError("the fixed Concordia scenario does not stream")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def test_fixed_scene_uses_original_concordia_and_the_jan_model_gateway(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    snapshot = ProjectStore(tmp_path / "fog-harbor").load()
    contexts = CharacterContextAssembler().assemble(snapshot)
    transport = QueuedTransport(
        _json(
            {
                "character_id": "chen-mo",
                "action": "检查灯塔机械装置",
                "target": "灯塔",
                "goal": "查明灯塔熄灭原因",
                "knowledge_basis": ["secret:chen-father-disappearance"],
                "recognized_risk": "可能暴露自己的调查",
            }
        ),
        _json(
            {
                "character_id": "lin-lan",
                "action": "呼叫客船降低航速",
                "target": "近港客船",
                "goal": "让客船安全进入雾港",
                "knowledge_basis": ["secret:lin-unfiled-duty-roster"],
                "recognized_risk": "备用航标可能不足",
            }
        ),
        _json(
            {
                "summary": "陈默检查装置。林岚要求客船降低航速。",
                "fact_candidates": [
                    {
                        "id": "fact:passenger-ship-slowed",
                        "statement": "客船开始减速。",
                        "visibility": "public",
                    }
                ],
                "world_changes": [
                    {
                        "target_type": "world",
                        "target_id": "world",
                        "field": "world_variables.round",
                        "old_value": 0,
                        "new_value": 1,
                        "reason": "统一结算两个角色的行动",
                    }
                ],
                "unresolved_consequences": ["灯塔仍未恢复"],
            }
        ),
        _json(
            {
                "summary": "陈默暂缓拆解装置, 林岚让客船在外港等待。",
                "fact_candidates": [
                    {
                        "id": "fact:passenger-ship-waited",
                        "statement": "客船在外港维持低速。",
                        "visibility": "public",
                    }
                ],
                "world_changes": [
                    {
                        "target_type": "world",
                        "target_id": "world",
                        "field": "world_variables.round",
                        "old_value": 0,
                        "new_value": 1,
                        "reason": "降低行动强度后推进回合",
                    }
                ],
                "unresolved_consequences": ["灯塔仍未恢复"],
            }
        ),
    )
    gateway = ModelGateway(
        ProfileRegistry(tmp_path / "model-profiles.json"),
        transport,
    )
    adapter = ConcordiaStoryAdapter(gateway)

    chen_intent = adapter.generate_intent(contexts["chen-mo"])
    lin_intent = adapter.generate_intent(contexts["lin-lan"])
    character_prompts = json.dumps(transport.calls[:2], ensure_ascii=False)
    assert "initial_incident" not in character_prompts
    assert '"round"' not in character_prompts
    assert "secret:lin-unfiled-duty-roster" not in json.dumps(
        transport.calls[0], ensure_ascii=False
    )
    outcome = adapter.resolve(
        snapshot.world,
        (chen_intent, lin_intent),
        snapshot.characters,
    )
    revised = adapter.revise(
        snapshot.world,
        (chen_intent, lin_intent),
        snapshot.characters,
        outcome,
        "让结果更克制, 不要立即拆解灯塔装置",
    )

    assert version("gdm-concordia") == "2.4.0"
    assert chen_intent.character_id == "chen-mo"
    assert lin_intent.character_id == "lin-lan"
    assert outcome.world_changes == (
        StateChange(
            target_type="world",
            target_id="world",
            field="world_variables.round",
            old_value=0,
            new_value=1,
            reason="统一结算两个角色的行动",
        ),
    )
    assert revised.summary == "陈默暂缓拆解装置, 林岚让客船在外港等待。"

    prompts = [call["messages"][0]["content"] for call in transport.calls]
    assert "secret:chen-father-disappearance" in prompts[0]
    assert "secret:lin-unfiled-duty-roster" not in prompts[0]
    assert "secret:lin-unfiled-duty-roster" in prompts[1]
    assert "secret:chen-father-disappearance" not in prompts[1]
    assert "检查灯塔机械装置" in prompts[2]
    assert "呼叫客船降低航速" in prompts[2]
    assert '"id": "chen-mo"' in prompts[2]
    assert '"id": "lin-lan"' in prompts[2]
    assert "Reuse an existing character" in prompts[2]
    assert "让结果更克制, 不要立即拆解灯塔装置" in prompts[3]
    assert "陈默检查装置。林岚要求客船降低航速。" in prompts[3]
    assert "do not merely append the instruction" in prompts[3]
    assert [call["model"] for call in transport.calls] == [
        "qwen3-8b",
        "qwen3-8b",
        "gpt-5-mini",
        "gpt-5-mini",
    ]
    assert all("response_format" in call for call in transport.calls)
    assert all(call["max_tokens"] == 8192 for call in transport.calls)
    assert transport.timeouts == [120, 120, 120, 120]
    resolver_schema = transport.calls[2]["response_format"]["json_schema"]["schema"]
    assert resolver_schema["properties"]["fact_candidates"]["maxItems"] == 8
    assert resolver_schema["properties"]["world_changes"]["maxItems"] == 8
    assert "only changes caused by this turn" in prompts[2]
    assert transport.contents == []
    assert gateway.usage.totals().requests == 4


def test_concordia_adapter_rejects_an_intent_for_another_character(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    snapshot = ProjectStore(tmp_path / "fog-harbor").load()
    context = CharacterContextAssembler().assemble(snapshot)["chen-mo"]
    transport = QueuedTransport(
        _json(
            {
                "character_id": "lin-lan",
                "action": "读取陈默的调查记录",
                "goal": "保护港务所声誉",
                "knowledge_basis": ["secret:chen-father-disappearance"],
            }
        )
    )
    adapter = ConcordiaStoryAdapter(
        ModelGateway(
            ProfileRegistry(tmp_path / "model-profiles.json"),
            transport,
        )
    )

    with pytest.raises(
        ValueError,
        match="Story Engine returned an intent for another character",
    ):
        adapter.generate_intent(context)


def test_resolver_accepts_multiline_world_outcome_json(tmp_path: Path) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    snapshot = ProjectStore(tmp_path / "fog-harbor").load()
    outcome = {
        "summary": "陈默检查灯塔装置, 林岚引导客船减速。",
        "fact_candidates": [
            {
                "id": "fact:passenger-ship-slowed",
                "statement": "客船开始减速。",
                "visibility": "public",
            }
        ],
        "world_changes": [
            {
                "target_type": "world",
                "target_id": "world",
                "field": "world_variables.round",
                "old_value": 0,
                "new_value": 1,
                "reason": "统一结算两个角色的行动",
            }
        ],
        "unresolved_consequences": ["灯塔仍未恢复"],
    }
    transport = QueuedTransport(json.dumps(outcome, ensure_ascii=False, indent=2))
    adapter = ConcordiaStoryAdapter(
        ModelGateway(ProfileRegistry(tmp_path / "model-profiles.json"), transport)
    )
    intents = (
        CharacterIntent(
            character_id="chen-mo",
            action="检查灯塔机械装置",
            goal="查明灯塔熄灭原因",
            knowledge_basis=("secret:chen-father-disappearance",),
        ),
        CharacterIntent(
            character_id="lin-lan",
            action="呼叫客船降低航速",
            goal="让客船安全进入雾港",
            knowledge_basis=("secret:lin-unfiled-duty-roster",),
        ),
    )

    resolved = adapter.resolve(snapshot.world, intents, snapshot.characters)

    assert resolved.summary == "陈默检查灯塔装置, 林岚引导客船减速。"
    assert resolved.world_changes == (
        StateChange(
            target_type="world",
            target_id="world",
            field="world_variables.round",
            old_value=0,
            new_value=1,
            reason="统一结算两个角色的行动",
        ),
    )


def test_concordia_choices_also_use_the_jan_model_gateway(tmp_path: Path) -> None:
    transport = QueuedTransport(_json({"choice": "等待"}))
    model = JanGatewayLanguageModel(
        ModelGateway(
            ProfileRegistry(tmp_path / "model-profiles.json"),
            transport,
        ),
        profile_id="character",
        task_type="character",
    )

    selected = model.sample_choice("下一步做什么?", ("调查", "等待"))

    assert selected == (1, "等待", {})
    assert transport.calls[0]["model"] == "qwen3-8b"
    assert transport.calls[0]["response_format"]["json_schema"]["schema"]["properties"][
        "choice"
    ] == {"enum": ["调查", "等待"]}
