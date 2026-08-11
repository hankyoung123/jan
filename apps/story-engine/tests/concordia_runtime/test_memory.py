from datetime import UTC, datetime

from story_engine.concordia_runtime.memory import (
    ConcordiaMemoryBank,
    ConcordiaMemoryCodec,
    rank_memory_records,
)
from story_engine.domain.memory import (
    MemoryQuery,
    MemoryRecord,
    MemoryRecordType,
    MemoryScope,
)


class CountingMemoryCodec(ConcordiaMemoryCodec):
    def __init__(self) -> None:
        self.decode_count = 0

    def decode(self, value: str) -> MemoryRecord | None:
        self.decode_count += 1
        return super().decode(value)


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
        for hit in actor_a.retrieve(MemoryQuery(query_text="brass key", limit=8))
    )
    b_text = " ".join(
        hit.record.text
        for hit in actor_b.retrieve(MemoryQuery(query_text="map pier", limit=8))
    )

    assert "brass key" in a_text
    assert "under the pier" not in a_text
    assert "under the pier" in b_text
    assert "brass key" not in b_text


def test_multiline_memory_round_trips_through_codec_and_replay_exactly() -> None:
    text = "First line.\nSecond line with {json-like} text.\n第三行保持原样。"
    record = _record("actor-a", text, record_id="a:multiline")
    codec = ConcordiaMemoryCodec()

    decoded = codec.decode(codec.encode(record))
    restored = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
    )
    restored.replay((record,))

    assert decoded is not None
    assert decoded.text == text
    assert restored.records()[0].text == record.text
    assert restored.records()[0].model_dump(exclude={"raw_text"}) == record.model_dump(
        exclude={"raw_text"}
    )
    assert restored.pending_records() == ()


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

    assert len(memory.records()) == 2


def test_pending_records_decode_only_the_uncommitted_tail() -> None:
    codec = CountingMemoryCodec()
    memory = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
        codec=codec,
    )
    memory.extend(
        _record(
            "actor-a",
            f"Committed memory {index}.",
            record_id=f"committed:{index}",
        )
        for index in range(200)
    )
    memory.mark_committed()
    codec.decode_count = 0
    memory.extend(
        (
            _record("actor-a", "New memory one.", record_id="pending:1"),
            _record("actor-a", "New memory two.", record_id="pending:2"),
        )
    )

    pending = memory.pending_records()

    assert codec.decode_count == 2
    assert [record.record_id for record in pending] == ["pending:1", "pending:2"]
    assert all(record.raw_text is None for record in pending)


def test_retrieval_exposes_lexical_recency_and_importance_scores() -> None:
    memory = ConcordiaMemoryBank(
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
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

    hits = memory.retrieve(
        MemoryQuery(query_text="lighthouse harbor", limit=2, before_step=101)
    )

    assert {hit.record.record_id for hit in hits} == {"old", "recent"}
    assert all(
        hit.lexical_score is not None and hit.lexical_score > 0 for hit in hits
    )
    assert hits[0].score > 0
    assert hits[0].recency_score is not None
    assert hits[0].importance_score is not None


def test_structural_rank_counts_every_requested_dimension() -> None:
    actor_only = _record(
        "actor-a", "The brass key was moved.", record_id="actor-only"
    ).model_copy(update={"actor_ids": ("actor-b",), "step": 5})
    fully_structured = actor_only.model_copy(
        update={
            "record_id": "actor-location-tag",
            "location_ids": ("locked-room",),
            "tags": ("investigate",),
        }
    )
    hits = rank_memory_records(
        (actor_only, fully_structured),
        MemoryQuery(
            query_text="brass key",
            actor_ids=("actor-b",),
            location_ids=("locked-room",),
            tags=("investigate",),
            before_step=6,
        ),
    )

    assert [hit.record.record_id for hit in hits] == [
        "actor-location-tag",
        "actor-only",
    ]
    assert abs((hits[0].score - hits[1].score) - (0.2 * 2 / 3)) < 1e-9


def test_raw_memory_record_types_do_not_include_derived_summaries() -> None:
    assert "reflection" not in {item.value for item in MemoryRecordType}
    assert "consolidation" not in {item.value for item in MemoryRecordType}
