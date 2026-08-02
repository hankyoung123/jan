from datetime import UTC, datetime

import pytest

from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.domain.memory import (
    MemoryQuery,
    MemoryRecord,
    MemoryRecordType,
    MemoryScope,
)


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
