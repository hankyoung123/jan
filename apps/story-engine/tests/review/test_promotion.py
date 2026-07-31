import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import pytest

from story_engine.domain.errors import InvalidTransitionError
from story_engine.domain.models import Character
from story_engine.models.contracts import ModelStreamChunk
from story_engine.models.gateway import ModelGateway
from story_engine.models.registry import ProfileRegistry
from story_engine.promotion.service import CharacterPromotionService
from story_engine.review.promotion import EditorPromotionReviewer
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.promotion_store import PromotionCandidateStore


class PromotionTransport:
    def __init__(self, output: dict[str, object]) -> None:
        self.output = output
        self.calls: list[Mapping[str, Any]] = []

    async def complete(
        self,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]:
        del timeout_seconds
        self.calls.append(payload)
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(self.output, ensure_ascii=False),
                    },
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


def _root(tmp_path: Path) -> Path:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    root = tmp_path / "fog-harbor"
    ProjectStore(root).save_character(
        Character(
            id="temporary-pilot",
            display_name="临时引航员",
            type="npc",
            identity="暴风雨中赶到港口的引航员",
            core_desire="让客船安全避开暗礁",
            current_goal="观察近港水流",
            known_fact_ids=("fact:near-harbor-reefs",),
            location="近港码头",
            version=2,
        ),
        overwrite=False,
    )
    return root


def _service(
    root: Path,
    tmp_path: Path,
    transport: PromotionTransport,
) -> CharacterPromotionService:
    reviewer = EditorPromotionReviewer(
        ModelGateway(ProfileRegistry(tmp_path / "profiles.json"), transport)
    )
    return CharacterPromotionService(root, reviewer=reviewer)


def _recommended_output() -> dict[str, object]:
    return {
        "review": {
            "mode": "promotion_review",
            "passed": True,
            "summary": "该人物已经形成独立目标并可能主动影响后续局势。",
            "issues": [],
        },
        "proposed_goal": "主动引导客船避开近港暗礁",
    }


def test_editor_creates_derived_promotion_candidate_without_promoting_npc(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    transport = PromotionTransport(_recommended_output())

    assessment = _service(root, tmp_path, transport).review("temporary-pilot")
    snapshot = ProjectStore(root).load()
    character = next(
        item for item in snapshot.characters if item.id == "temporary-pilot"
    )

    assert assessment.review.mode == "promotion_review"
    assert assessment.candidate is not None
    assert assessment.candidate.id == "promotion-temporary-pilot-v2"
    assert assessment.candidate.status == "pending"
    assert character.type == "npc"
    assert (root / "characters/npc/temporary-pilot.md").exists()
    assert not (root / "characters/active/temporary-pilot.md").exists()
    assert EventStore(root).list_events() == ()
    assert PromotionCandidateStore(root).load("temporary-pilot") == assessment.candidate
    assert transport.calls[0]["model"] == "gpt-5-mini"
    prompt = transport.calls[0]["messages"][0]["content"]
    assert "promotion_review" in prompt
    assert "临时引航员" in prompt
    assert "not user approval" in prompt


def test_rejected_reassessment_removes_stale_derived_suggestion(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    transport = PromotionTransport(_recommended_output())
    service = _service(root, tmp_path, transport)
    service.review("temporary-pilot")
    transport.output = {
        "review": {
            "mode": "promotion_review",
            "passed": False,
            "summary": "该人物尚未形成独立目标。",
            "issues": [],
        },
        "proposed_goal": None,
    }

    assessment = service.review("temporary-pilot")

    assert assessment.candidate is None
    assert not (root / ".story-engine/reviews/promotion-temporary-pilot.json").exists()
    assert (
        next(
            item
            for item in ProjectStore(root).load().characters
            if item.id == "temporary-pilot"
        ).type
        == "npc"
    )


def test_only_explicit_confirmation_commits_promotion(tmp_path: Path) -> None:
    root = _root(tmp_path)
    transport = PromotionTransport(_recommended_output())
    service = _service(root, tmp_path, transport)
    assessment = service.review("temporary-pilot")
    assert assessment.candidate is not None

    result = service.confirm("temporary-pilot", assessment.candidate.id)

    assert result.character.type == "active"
    assert result.candidate.status == "committed"
    assert PromotionCandidateStore(root).load("temporary-pilot").status == "committed"
    assert EventStore(root).list_events() == (result.event,)
    with pytest.raises(InvalidTransitionError, match="pending promotion"):
        service.confirm("temporary-pilot", assessment.candidate.id)
