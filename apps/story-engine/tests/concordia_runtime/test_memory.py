from datetime import UTC, datetime

import numpy as np
import pytest

from story_engine.concordia_runtime.factory import (
    ConcordiaGameMasterActor,
    ConcordiaStoryActor,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.memory_lifecycle import ConcordiaMemoryLifecycle
from story_engine.domain.action import EntityRole
from story_engine.domain.memory import (
    MemoryQuery,
    MemoryRecord,
    MemoryRecordType,
    MemoryScope,
)
from story_engine.domain.projection import SimulationBoundary


class _FakeEntity:
    def __init__(self, name: str) -> None:
        self.name = name


class _LifecycleModel:
    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.prompts: list[str] = []

    def sample_text(self, prompt: str, **_: object) -> str:
        self.prompts.append(prompt)
        return f"{self.prefix}: stable fact and open thread"


def _record(owner_id: str, text: str, *, record_id: str) -> MemoryRecord:
    return MemoryRecord(
        record_id=record_id,
        record_type=MemoryRecordType.PREMISE,
        scope=MemoryScope.CHARACTER,
        owner_id=owner_id,
        session_id="session:1",
        branch_id="main",
        step=0,
        text=text,
        content_locale="en-US",
        created_at=datetime.now(UTC),
        visible_to=(owner_id,),
    )


def test_character_memory_banks_are_isolated() -> None:
    actor_a = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
    )
    actor_b = ConcordiaMemoryBank(
        owner_id="actor-b",
        scope=MemoryScope.CHARACTER,
    )
    actor_a.add(_record("actor-a", "A knows the brass key code.", record_id="a:1"))
    actor_b.add(_record("actor-b", "B hid a map under the pier.", record_id="b:1"))

    a_text = " ".join(
        hit.record.text
        for hit in actor_a.retrieve(MemoryQuery(query_text="secret", limit=8))
    )
    b_text = " ".join(
        hit.record.text
        for hit in actor_b.retrieve(MemoryQuery(query_text="secret", limit=8))
    )

    assert "brass key" in a_text
    assert "under the pier" not in a_text
    assert "under the pier" in b_text
    assert "brass key" not in b_text


def test_memory_snapshot_restores_exact_hash_and_rejects_tampering() -> None:
    original = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
    )
    original.add(_record("actor-a", "The bell rang once.", record_id="a:1"))
    snapshot = original.snapshot()
    restored = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
    )

    restored.restore(snapshot)

    assert restored.snapshot().state_hash == snapshot.state_hash
    assert restored.retrieve_recent(limit=1)[0].text == "The bell rang once."

    tampered = snapshot.model_copy(update={"state": {"memory_bank": "broken"}})
    with pytest.raises(ValueError, match="hash mismatch"):
        restored.restore(tampered)


def test_game_master_memory_allows_repeated_world_events() -> None:
    memory = ConcordiaMemoryBank(
        owner_id="gm",
        scope=MemoryScope.GAME_MASTER,
    )
    event = MemoryRecord(
        record_id="event:1",
        record_type=MemoryRecordType.WORLD_EVENT,
        scope=MemoryScope.GAME_MASTER,
        owner_id="gm",
        session_id="session:1",
        branch_id="main",
        step=1,
        text="The clock strikes.",
        content_locale="en-US",
        created_at=datetime.now(UTC),
    )

    memory.add(event)
    memory.add(event)

    assert memory.snapshot().record_count == 2


def test_retrieval_exposes_semantic_recency_and_importance_scores() -> None:
    memory = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
        embedder=lambda _: np.asarray([1.0, 0.0]),
    )
    old_important = _record(
        "actor-a",
        "The old lighthouse oath.",
        record_id="old",
    ).model_copy(update={"step": 1, "importance": 1.0})
    recent = _record(
        "actor-a",
        "The fresh harbor report.",
        record_id="recent",
    ).model_copy(update={"step": 100, "importance": 0.2})
    memory.extend((old_important, recent))

    hits = memory.retrieve(MemoryQuery(query_text="harbor", limit=2, before_step=101))

    assert {hit.record.record_id for hit in hits} == {"old", "recent"}
    assert all(hit.semantic_score == 1.0 for hit in hits)
    assert hits[0].score > 0
    assert hits[0].recency_score is not None
    assert hits[0].importance_score is not None


def test_scene_boundary_reflects_and_chapter_boundary_consolidates_privately() -> None:
    actor_memory = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
    )
    gm_memory = ConcordiaMemoryBank(
        owner_id="gm",
        scope=MemoryScope.GAME_MASTER,
    )
    actor_memory.add(_record("actor-a", "The signal was cut.", record_id="actor:1"))
    gm_memory.add(
        MemoryRecord(
            record_id="gm:1",
            record_type=MemoryRecordType.WORLD_EVENT,
            scope=MemoryScope.GAME_MASTER,
            owner_id="gm",
            session_id="session:1",
            branch_id="main",
            step=1,
            text="The signal wire was deliberately severed.",
            content_locale="en-US",
            created_at=datetime.now(UTC),
        )
    )
    actor = ConcordiaStoryActor(
        _FakeEntity("actor-a"),  # type: ignore[arg-type]
        role=EntityRole.CHARACTER,
        memory=actor_memory,
    )
    game_master = ConcordiaGameMasterActor(
        _FakeEntity("gm"),  # type: ignore[arg-type]
        role=EntityRole.GAME_MASTER,
        memory=gm_memory,
    )
    reflection = _LifecycleModel("reflection")
    consolidation = _LifecycleModel("consolidation")
    lifecycle = ConcordiaMemoryLifecycle(
        reflection_model=reflection,  # type: ignore[arg-type]
        consolidation_model=consolidation,  # type: ignore[arg-type]
        content_locale="en-US",
    )

    written = lifecycle.process_boundary(
        session_id="session:1",
        branch_id="main",
        step=2,
        boundary=SimulationBoundary.CHAPTER,
        acting_actor=actor,
        game_master=game_master,
    )

    assert [record.record_type for record in written] == [
        MemoryRecordType.REFLECTION,
        MemoryRecordType.REFLECTION,
        MemoryRecordType.CONSOLIDATION,
        MemoryRecordType.CONSOLIDATION,
    ]
    actor_records = [record for record in written if record.owner_id == "actor-a"]
    gm_records = [record for record in written if record.owner_id == "gm"]
    assert all(record.visible_to == ("actor-a",) for record in actor_records)
    assert all(record.visible_to == () for record in gm_records)
    assert len(reflection.prompts) == 2
    assert len(consolidation.prompts) == 2
