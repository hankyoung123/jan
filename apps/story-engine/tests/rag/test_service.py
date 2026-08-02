import json
import shutil
from pathlib import Path

import pytest

from story_engine.rag.models import RagSearchRequest, RetrievalScope
from story_engine.rag.service import RagService
from story_engine.submission.service import SubmissionService, fog_harbor_submission


def _project(tmp_path: Path) -> Path:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    root = tmp_path / "fog-harbor"
    (root / "sources/style.md").write_text(
        "# 风格规范\n\n叙事保持克制, 不直接解释人物动机。\n",
        encoding="utf-8",
    )
    (root / "sources/research.md").write_text(
        "# 研究笔记\n\n林岚尚未公开的访谈记录。\n",
        encoding="utf-8",
    )
    return root


def test_markdown_index_uses_only_seed_documents_and_writer_safe_sources(
    tmp_path: Path,
) -> None:
    service = RagService(_project(tmp_path))

    index = service.rebuild_index()
    fact = service.search(
        RagSearchRequest(
            exact_id="fact:lighthouse-controls-night-navigation",
            scope=RetrievalScope(kind="writer"),
        )
    )
    style = service.search(
        RagSearchRequest(
            exact_id="source:style",
            scope=RetrievalScope(kind="writer"),
        )
    )
    research = service.search(
        RagSearchRequest(
            exact_id="source:research",
            scope=RetrievalScope(kind="writer"),
        )
    )

    assert index.document_count >= 7
    assert index.chunk_count == len(index.chunks)
    assert fact.hits
    assert {hit.source_type for hit in fact.hits} == {"fact"}
    assert style.hits and any("叙事保持克制" in hit.content for hit in style.hits)
    assert research.hits == ()
    assert all(chunk.source_type not in {"event", "scene"} for chunk in index.chunks)


def test_character_scope_filters_private_seed_facts_before_scoring(
    tmp_path: Path,
) -> None:
    service = RagService(_project(tmp_path))
    service.rebuild_index()

    owner = service.search(
        RagSearchRequest(
            exact_id="secret:lin-unfiled-duty-roster",
            scope=RetrievalScope(kind="character", character_id="lin-lan"),
        )
    )
    other = service.search(
        RagSearchRequest(
            exact_id="secret:lin-unfiled-duty-roster",
            scope=RetrievalScope(kind="character", character_id="chen-mo"),
        )
    )
    other_card = service.search(
        RagSearchRequest(
            exact_id="lin-lan",
            scope=RetrievalScope(kind="character", character_id="chen-mo"),
        )
    )

    assert owner.hits
    assert other.hits == ()
    assert other_card.hits == ()


def test_deleted_index_rebuilds_from_canonical_seed_markdown(tmp_path: Path) -> None:
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

    rebuilt = json.loads(
        (root / ".story-engine/index/rag-v1.json").read_text(encoding="utf-8")
    )
    assert result.hits
    assert tuple(rebuilt["chunks"]) == first_chunks
    assert rebuilt["fingerprint"] == first.fingerprint


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
        RagSearchRequest(query="灯塔", scope=RetrievalScope(kind="editorial"))
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
