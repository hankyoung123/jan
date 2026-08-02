import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from story_engine.domain.models import Fact, StoryEvent
from story_engine.rag.models import RagSearchRequest, RetrievalScope
from story_engine.rag.service import RagService
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.fact_store import FactStore
from story_engine.workspace.project_store import ProjectStore


def _project(tmp_path: Path) -> Path:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    root = tmp_path / "fog-harbor"
    introduced_at = datetime(2026, 7, 31, 4, 0, tzinfo=UTC)
    facts = (
        Fact(
            id="fact:fresh-scratches",
            statement="灯芯槽上有新鲜刮痕。",
            visibility="public",
            source_event_id="event-000001",
            introduced_at=introduced_at,
        ),
        Fact(
            id="secret:spare-key-scratches",
            statement="刮痕来自林岚保管的备用钥匙。",
            visibility="secret",
            known_by=("lin-lan",),
            source_event_id="event-000001",
            introduced_at=introduced_at,
        ),
        Fact(
            id="fact:north-breakwater-stable",
            statement="北侧防波堤暂时稳定。",
            visibility="private",
            known_by=("lin-lan",),
            source_event_id="event-000002",
            introduced_at=introduced_at,
        ),
        Fact(
            id="secret:north-door-code",
            statement="暗门密码为7341。",
            visibility="secret",
            known_by=("chen-mo",),
            source_event_id="event-000002",
            introduced_at=introduced_at,
        ),
    )
    fact_store = FactStore(root)
    for fact in facts:
        fact_store.save(fact)
    store = ProjectStore(root)
    snapshot = store.load()
    store.save_world(
        snapshot.world.model_copy(
            update={
                "public_fact_ids": (
                    *snapshot.world.public_fact_ids,
                    "fact:fresh-scratches",
                )
            }
        )
    )
    for character in snapshot.characters:
        learned = tuple(
            fact.id
            for fact in facts
            if fact.visibility != "public" and character.id in fact.known_by
        )
        store.save_character(
            character.model_copy(
                update={"known_fact_ids": (*character.known_fact_ids, *learned)}
            )
        )
    events = EventStore(root)
    events.append(
        StoryEvent(
            id="event-000001",
            sequence=1,
            occurred_at=datetime(2026, 7, 31, 4, 0, tzinfo=UTC),
            summary="陈默抵达灯塔并发现灯芯槽上的新鲜刮痕。",
            participants=("chen-mo",),
            fact_ids=("fact:fresh-scratches", "secret:spare-key-scratches"),
            source_record_id="session:one",
            approved_by_user=True,
        )
    )
    events.append(
        StoryEvent(
            id="event-000002",
            sequence=2,
            occurred_at=datetime(2026, 7, 31, 4, 5, tzinfo=UTC),
            summary="林岚独自检查了北侧防波堤。",
            participants=("lin-lan",),
            fact_ids=("fact:north-breakwater-stable", "secret:north-door-code"),
            source_record_id="session:two",
            approved_by_user=True,
        )
    )
    (root / "sources/style.md").write_text(
        "# 风格规范\n\n叙事保持克制, 不直接解释人物动机。\n",
        encoding="utf-8",
    )
    (root / "sources/research.md").write_text(
        "# 研究笔记\n\n林岚尚未公开的访谈记录。\n",
        encoding="utf-8",
    )
    return root


def test_markdown_index_supports_exact_id_and_bm25_with_citations(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    service = RagService(root)

    index = service.rebuild_index()
    exact = service.search(
        RagSearchRequest(
            exact_id="event-000001",
            scope=RetrievalScope(kind="writer"),
        )
    )
    ranked = service.search(
        RagSearchRequest(
            query="灯芯槽 新鲜刮痕",
            scope=RetrievalScope(kind="editorial"),
            limit=4,
        )
    )
    writer_world = service.search(
        RagSearchRequest(
            exact_id="world",
            scope=RetrievalScope(kind="writer"),
            limit=20,
        )
    )
    writer_source = service.search(
        RagSearchRequest(
            exact_id="source:style",
            scope=RetrievalScope(kind="writer"),
        )
    )
    writer_research = service.search(
        RagSearchRequest(
            exact_id="source:research",
            scope=RetrievalScope(kind="writer"),
        )
    )

    assert index.document_count >= 8
    assert index.chunk_count == len(index.chunks)
    assert exact.hits
    assert {hit.source_id for hit in exact.hits} == {"event-000001"}
    assert all(hit.retrieval_mode == "exact" for hit in exact.hits)
    assert all("备用钥匙" not in hit.content for hit in exact.hits)
    assert all(hit.heading != "Variables" for hit in writer_world.hits)
    assert all("world_variables" not in hit.content for hit in writer_world.hits)
    assert writer_source.hits
    assert all(hit.source_id == "source:style" for hit in writer_source.hits)
    assert any("叙事保持克制" in hit.content for hit in writer_source.hits)
    assert writer_research.hits == ()
    assert ranked.hits[0].source_id in {
        "event-000001",
        "fact:fresh-scratches",
    }
    assert all(
        hit.chunk_id
        and hit.source_type
        and hit.source_id
        and hit.source_path
        and hit.heading
        and hit.permission_scope == "editorial"
        for hit in ranked.hits
    )


def test_character_scope_filters_unauthorized_markdown_before_scoring(
    tmp_path: Path,
) -> None:
    service = RagService(_project(tmp_path))
    service.rebuild_index()

    chen_private = service.search(
        RagSearchRequest(
            query="北侧防波堤",
            scope=RetrievalScope(kind="character", character_id="chen-mo"),
        )
    )
    lin_experienced = service.search(
        RagSearchRequest(
            query="北侧防波堤",
            scope=RetrievalScope(kind="character", character_id="lin-lan"),
        )
    )
    lin_hidden = service.search(
        RagSearchRequest(
            query="暗门密码 7341",
            scope=RetrievalScope(kind="character", character_id="lin-lan"),
        )
    )
    editorial_hidden = service.search(
        RagSearchRequest(
            query="暗门密码 7341",
            scope=RetrievalScope(kind="editorial"),
        )
    )
    other_character_card = service.search(
        RagSearchRequest(
            exact_id="lin-lan",
            scope=RetrievalScope(kind="character", character_id="chen-mo"),
        )
    )

    assert chen_private.hits == ()
    assert lin_experienced.hits
    assert {hit.source_id for hit in lin_experienced.hits} == {
        "event-000002",
        "fact:north-breakwater-stable",
        "lin-lan",
    }
    assert lin_hidden.hits == ()
    assert any("7341" in hit.content for hit in editorial_hidden.hits)
    assert other_character_card.hits == ()


def test_deleted_index_rebuilds_completely_from_canonical_markdown(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    service = RagService(root)
    first = service.rebuild_index()
    first_chunks = tuple(chunk.model_dump(mode="json") for chunk in first.chunks)

    shutil.rmtree(root / ".story-engine/index")
    result = service.search(
        RagSearchRequest(
            query="雾港 灯塔",
            scope=RetrievalScope(kind="editorial"),
        )
    )

    rebuilt_path = root / ".story-engine/index/rag-v1.json"
    assert result.hits
    assert rebuilt_path.is_file()
    rebuilt = json.loads(rebuilt_path.read_text(encoding="utf-8"))
    assert tuple(rebuilt["chunks"]) == first_chunks
    assert rebuilt["fingerprint"] == first.fingerprint


def test_every_returned_chunk_has_stable_source_metadata(tmp_path: Path) -> None:
    service = RagService(_project(tmp_path))

    result = service.search(
        RagSearchRequest(
            query="风格 叙事 克制 灯塔",
            scope=RetrievalScope(kind="writer"),
            limit=10,
        )
    )

    assert result.hits
    for hit in result.hits:
        assert hit.chunk_id.startswith(f"{hit.source_id}#")
        assert hit.source_path.endswith(".md")
        assert hit.content.strip()
        assert hit.score > 0


def test_unchanged_search_does_not_reread_canonical_markdown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path)
    service = RagService(root)
    service.rebuild_index()
    real_read_bytes = Path.read_bytes
    markdown_reads: list[Path] = []

    def track_read_bytes(path: Path) -> bytes:
        if path.suffix == ".md" and root in path.parents:
            markdown_reads.append(path)
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", track_read_bytes)

    result = service.search(
        RagSearchRequest(
            query="灯塔",
            scope=RetrievalScope(kind="editorial"),
        )
    )

    assert result.hits
    assert markdown_reads == []


def test_rag_incrementally_rechunks_only_changed_document(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _project(tmp_path)
    service = RagService(root)
    original = service.rebuild_index()
    changed_paths: list[str] = []
    real_chunks_for = service._chunks_for

    def track_chunks(path: Path):
        changed_paths.append(path.relative_to(root).as_posix())
        return real_chunks_for(path)

    monkeypatch.setattr(service, "_chunks_for", track_chunks)
    style_path = root / "sources/style.md"
    style_path.write_text(
        style_path.read_text(encoding="utf-8") + "\n避免夸张修辞。\n",
        encoding="utf-8",
    )

    updated = service.ensure_index()

    assert changed_paths == ["sources/style.md"]
    assert updated.fingerprint != original.fingerprint
    assert updated.document_count == original.document_count
