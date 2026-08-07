from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.projection import (
    EventVisibility,
    ResolvedEvent,
    SimulationBoundary,
)
from story_engine.manuscript import source as source_module
from story_engine.manuscript.models import (
    ManuscriptSourceCandidate,
    ManuscriptSourceManifest,
    SourceSelectionResult,
    WriterSourceSelection,
)
from story_engine.manuscript.source import (
    MAX_CONTINUITY_CHARS,
    MAX_SOURCE_CHARS,
    MAX_SOURCE_MEMORY_RECORDS,
    CandidateSourceBuilder,
    ManuscriptContextBuilder,
    SourceSelectionNotReadyError,
    SourceSelectionValidator,
)


def _candidate(
    *,
    source_id: str,
    checkpoint_id: str,
    from_step: int,
    to_step: int,
    estimated_chars: int,
    event_id: str,
    status: str = "available",
) -> ManuscriptSourceCandidate:
    return ManuscriptSourceCandidate(
        source_id=source_id,
        branch_id="main",
        checkpoint_id=checkpoint_id,
        from_step=from_step,
        to_step=to_step,
        boundary=SimulationBoundary.SCENE,
        title_hint=f"Step {from_step}",
        event_summary_text="x" * estimated_chars,
        event_ids=(event_id,),
        estimated_chars=estimated_chars,
        available_viewpoint_ids=("actor-a",),
        wiki_version_id=checkpoint_id,
        status=status,  # type: ignore[arg-type]
    )


def _builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    candidates: tuple[ManuscriptSourceCandidate, ...],
) -> CandidateSourceBuilder:
    builder = CandidateSourceBuilder(tmp_path, "main")
    branch = SimpleNamespace(
        project_id="project",
        branch_id="main",
        head_checkpoint_id="checkpoint-2",
    )
    monkeypatch.setattr(builder, "_branch", lambda: branch)
    monkeypatch.setattr(
        builder,
        "_head",
        lambda _branch: ("checkpoint-2", SimpleNamespace()),
    )
    monkeypatch.setattr(
        builder.checkpoints,
        "lineage",
        lambda _checkpoint_id: ("checkpoint-1", "checkpoint-2"),
    )
    monkeypatch.setattr(builder, "all_candidates", lambda: candidates)
    return builder


def test_list_candidates_keeps_newest_contiguous_tail_within_character_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = tuple(
        _candidate(
            source_id=f"source-{index}",
            checkpoint_id=f"checkpoint-{index + 1}",
            from_step=index + 1,
            to_step=index + 1,
            estimated_chars=70_000 if index < 2 else 1_000,
            event_id=f"event-{index}",
        )
        for index in range(3)
    )
    builder = CandidateSourceBuilder(tmp_path, "main")
    monkeypatch.setattr(builder, "all_candidates", lambda: candidates)

    selected = builder.list_candidates()

    assert tuple(item.source_id for item in selected) == (
        "source-1",
        "source-2",
    )
    assert sum(item.estimated_chars for item in selected) <= MAX_SOURCE_CHARS


def test_manifest_rejects_aggregate_source_character_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = (
        _candidate(
            source_id="source-1",
            checkpoint_id="checkpoint-1",
            from_step=1,
            to_step=1,
            estimated_chars=70_000,
            event_id="event-1",
        ),
        _candidate(
            source_id="source-2",
            checkpoint_id="checkpoint-2",
            from_step=2,
            to_step=2,
            estimated_chars=70_000,
            event_id="event-2",
        ),
    )
    builder = _builder(tmp_path, monkeypatch, candidates)

    with pytest.raises(ValueError, match="character budget"):
        builder._validate_candidate_sequence(candidates, allow_covered=False)


def test_multi_source_selection_must_be_contiguous(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = (
        _candidate(
            source_id="source-1",
            checkpoint_id="checkpoint-1",
            from_step=1,
            to_step=1,
            estimated_chars=10,
            event_id="event-1",
        ),
        _candidate(
            source_id="source-3",
            checkpoint_id="checkpoint-2",
            from_step=3,
            to_step=3,
            estimated_chars=10,
            event_id="event-3",
        ),
    )
    builder = _builder(tmp_path, monkeypatch, candidates)

    with pytest.raises(ValueError, match="contiguous"):
        builder._validate_candidate_sequence(candidates, allow_covered=False)


def test_multi_source_selection_cannot_cross_branches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(
        source_id="source-other",
        checkpoint_id="checkpoint-1",
        from_step=1,
        to_step=1,
        estimated_chars=10,
        event_id="event-1",
    ).model_copy(update={"branch_id": "alternate"})
    builder = _builder(tmp_path, monkeypatch, (candidate,))

    with pytest.raises(ValueError, match="branches"):
        builder._validate_candidate_sequence((candidate,), allow_covered=False)


def test_writer_cannot_select_a_covered_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    covered = _candidate(
        source_id="source-covered",
        checkpoint_id="checkpoint-1",
        from_step=1,
        to_step=1,
        estimated_chars=10,
        event_id="event-1",
        status="covered",
    )
    builder = CandidateSourceBuilder(tmp_path, "main")
    monkeypatch.setattr(builder, "all_candidates", lambda: (covered,))

    with pytest.raises(ValueError, match="covered"):
        SourceSelectionValidator(builder).resolve(
            WriterSourceSelection(),
            viewpoint_actor_id=None,
            writer_result=SourceSelectionResult(
                decision="ready",
                source_ids=(covered.source_id,),
                reason="selected covered range",
            ),
        )


def test_writer_not_ready_is_not_resolved_into_a_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = CandidateSourceBuilder(tmp_path, "main")
    monkeypatch.setattr(builder, "all_candidates", lambda: ())

    with pytest.raises(SourceSelectionNotReadyError, match="not enough"):
        SourceSelectionValidator(builder).resolve(
            WriterSourceSelection(),
            viewpoint_actor_id=None,
            writer_result=SourceSelectionResult(
                decision="not_ready",
                reason="not enough events for a coherent scene",
            ),
        )


def _event(
    event_id: str,
    visibility: EventVisibility,
    *,
    participant_ids: tuple[str, ...] = (),
    observer_ids: tuple[str, ...] = (),
) -> ResolvedEvent:
    return ResolvedEvent(
        event_id=event_id,
        session_id="session-1",
        step=1,
        actor_id="actor-a",
        event_text=event_id,
        visibility=visibility,
        participant_ids=participant_ids,
        observer_ids=observer_ids,
        content_locale="en-US",
        occurred_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


def test_automatic_viewpoint_excludes_private_events() -> None:
    events = (
        _event("public", EventVisibility.PUBLIC),
        _event(
            "participant-a",
            EventVisibility.PARTICIPANTS,
            participant_ids=("actor-a",),
        ),
        _event(
            "participant-b",
            EventVisibility.PARTICIPANTS,
            participant_ids=("actor-b",),
        ),
        _event(
            "restricted-a",
            EventVisibility.RESTRICTED,
            observer_ids=("actor-a",),
        ),
    )

    automatic = ManuscriptContextBuilder._events_for_viewpoint(events, None)
    actor_a = ManuscriptContextBuilder._events_for_viewpoint(events, "actor-a")

    assert tuple(item.event_id for item in automatic) == ("public",)
    assert tuple(item.event_id for item in actor_a) == (
        "public",
        "participant-a",
        "restricted-a",
    )


def test_automatic_manifest_resolves_the_principal_acting_actor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate(
        source_id="source-1",
        checkpoint_id="checkpoint-1",
        from_step=1,
        to_step=1,
        estimated_chars=10,
        event_id="event-1",
    )
    builder = _builder(tmp_path, monkeypatch, (candidate,))
    event = _event(
        "event-1", EventVisibility.PARTICIPANTS, participant_ids=("actor-a",)
    )
    record = SimpleNamespace(
        result=SimpleNamespace(
            step=1,
            resolved_turn=SimpleNamespace(acting_actor_id="actor-a", events=(event,)),
        )
    )
    monkeypatch.setattr(
        builder,
        "_snapshot_for",
        lambda _checkpoint_id: SimpleNamespace(
            roster_actor_ids=("actor-a",),
            memory_snapshots={"actor-a": object()},
        ),
    )
    monkeypatch.setattr(builder, "_records", lambda *_args: (record,))
    monkeypatch.setattr(builder, "_memory_ids", lambda *_args, **_kwargs: ())

    manifest = builder.build_manifest((candidate,), viewpoint_actor_id=None)

    assert manifest.viewpoint_actor_id == "actor-a"


def _memory(
    record_id: str,
    *,
    owner_id: str = "shared",
    scope: MemoryScope = MemoryScope.SHARED,
    step: int = 1,
) -> MemoryRecord:
    return MemoryRecord(
        record_id=record_id,
        record_type=MemoryRecordType.OBSERVATION,
        scope=scope,
        owner_id=owner_id,
        session_id="session-1",
        branch_id="main",
        step=step,
        text=record_id,
        content_locale="en-US",
        created_at=datetime(2026, 8, 4, tzinfo=UTC),
    )


def test_foreign_private_memory_is_excluded() -> None:
    foreign = _memory(
        "foreign",
        owner_id="actor-b",
        scope=MemoryScope.CHARACTER,
    )
    own = _memory(
        "own",
        owner_id="actor-a",
        scope=MemoryScope.CHARACTER,
    )

    assert not CandidateSourceBuilder._memory_visible(foreign, "actor-a")
    assert CandidateSourceBuilder._memory_visible(own, "actor-a")


def test_memory_context_is_capped_at_maximum_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = ManuscriptContextBuilder(tmp_path, "main")
    records = tuple(
        _memory(f"memory-{index:03}") for index in range(MAX_SOURCE_MEMORY_RECORDS + 2)
    )
    monkeypatch.setattr(builder, "_decode_snapshot_memories", lambda _snapshot: records)
    source = ManuscriptSourceManifest(
        project_id="project",
        branch_id="main",
        checkpoint_id="checkpoint-1",
        source_ids=("source-1",),
        from_step=1,
        to_step=1,
        event_ids=("event-1",),
        memory_ids=tuple(
            record.record_id for record in records[:MAX_SOURCE_MEMORY_RECORDS]
        ),
        wiki_version_id="seed",
    )

    selected = builder._facts_memories(SimpleNamespace(), source)

    assert len(selected) == MAX_SOURCE_MEMORY_RECORDS
    assert tuple(record.record_id for record in selected) == source.memory_ids


def test_continuity_excerpt_is_capped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = ManuscriptContextBuilder(tmp_path, "main")
    previous = SimpleNamespace(
        id="scene-1",
        chapter_id="chapter-1",
        title="Previous scene",
        body="x" * (MAX_CONTINUITY_CHARS + 100),
    )
    monkeypatch.setattr(builder.scenes, "list_scenes", lambda: (previous,))

    continuity = builder._continuity(chapter_id="chapter-1")

    assert continuity.previous_scene_id == "scene-1"
    assert len(continuity.previous_scene_excerpt) == MAX_CONTINUITY_CHARS


def test_same_manifest_rebuilds_identical_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    builder = ManuscriptContextBuilder(tmp_path, "main")
    event = _event("event-1", EventVisibility.PUBLIC)
    record = SimpleNamespace(
        result=SimpleNamespace(
            branch_id="main",
            step=1,
            resolved_turn=SimpleNamespace(events=(event,)),
        )
    )
    project = SimpleNamespace(
        id="project",
        title="Project",
        genre="mystery",
        theme="truth",
        tone="restrained",
    )
    monkeypatch.setattr(builder.sources, "validate_manifest", lambda _source: None)
    monkeypatch.setattr(
        builder,
        "_branch",
        lambda: SimpleNamespace(project_id="project"),
    )
    monkeypatch.setattr(builder.logs, "reachable", lambda *_args: (record,))
    monkeypatch.setattr(
        builder.checkpoints,
        "load",
        lambda _checkpoint_id: SimpleNamespace(
            content_locale="en-US",
            memory_snapshots={},
        ),
    )
    monkeypatch.setattr(builder, "_wiki_branch", lambda *_args: "main")
    monkeypatch.setattr(
        builder,
        "_continuity",
        lambda **_kwargs: source_module.ManuscriptContinuityContext(),
    )
    monkeypatch.setattr(
        builder.projects,
        "load",
        lambda: SimpleNamespace(project=project),
    )

    class FakeWikiContextBuilder:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def writer(self, *_args, **_kwargs) -> SimpleNamespace:
            return SimpleNamespace(content="historical wiki", manifest=())

    monkeypatch.setattr(source_module, "WikiContextBuilder", FakeWikiContextBuilder)
    source = ManuscriptSourceManifest(
        project_id="project",
        branch_id="main",
        checkpoint_id="checkpoint-1",
        source_ids=("source-1",),
        from_step=1,
        to_step=1,
        event_ids=("event-1",),
        wiki_version_id="seed",
    )
    intent = source_module.ManuscriptWritingIntent(chapter_id="chapter-1")

    first, _ = builder.build_context(source, intent=intent, selection_reason="manual")
    second, _ = builder.build_context(source, intent=intent, selection_reason="manual")

    assert first.facts == second.facts


@pytest.mark.parametrize("mutation", ["deleted", "modified"])
def test_persisted_manifest_lineage_must_match_current_candidates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    original = _candidate(
        source_id="source-1",
        checkpoint_id="checkpoint-1",
        from_step=1,
        to_step=1,
        estimated_chars=100,
        event_id="event-1",
        status="covered",
    )
    current = (
        ()
        if mutation == "deleted"
        else (original.model_copy(update={"event_ids": ("event-changed",)}),)
    )
    builder = _builder(tmp_path, monkeypatch, current)
    source = ManuscriptSourceManifest(
        project_id="project",
        branch_id="main",
        checkpoint_id="checkpoint-1",
        source_ids=(original.source_id,),
        from_step=1,
        to_step=1,
        event_ids=original.event_ids,
        wiki_version_id=original.wiki_version_id,
        viewpoint_actor_id="actor-a",
    )

    with pytest.raises(ValueError, match=r"lineage|events"):
        builder.validate_manifest(source)
