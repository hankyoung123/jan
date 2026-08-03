from datetime import UTC, datetime
from pathlib import Path

import pytest

from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.wiki import WikiPatch, WikiPatchOperation
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.wiki.context import WikiContextBuilder
from story_engine.wiki.store import WikiStore


def _root(tmp_path: Path) -> Path:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    return tmp_path / "fog-harbor"


def test_seed_creates_schema_world_and_independent_character_indexes(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    store = WikiStore(root, "main")

    assert store.schema_path.is_file()
    assert store.load_page("world/index.md").source_ids
    chen = store.load_page("characters/chen-mo/beliefs.md")
    lin = store.load_page("characters/lin-lan/beliefs.md")
    assert "父亲" in chen.content
    assert "值班表" not in chen.content
    assert "值班表" in lin.content
    assert "父亲" not in lin.content
    assert store.load_page("characters/chen-mo/index.md").subject_id == "chen-mo"


def test_fork_uses_wiki_version_at_or_before_the_checkpoint(tmp_path: Path) -> None:
    root = _root(tmp_path)
    main = WikiStore(root, "main")
    main.apply_patches(
        (
            WikiPatch(
                path="world/state.md",
                operation=WikiPatchOperation.APPEND_HISTORY,
                content="## Scene 10\n\nThe bell rings once.",
                source_ids=("event:scene:10",),
            ),
        ),
        checkpoint_id="checkpoint:ten",
        step=10,
    )
    main.apply_patches(
        (
            WikiPatch(
                path="world/state.md",
                operation=WikiPatchOperation.APPEND_HISTORY,
                content="## Scene 20\n\nThe bell rings twice.",
                source_ids=("event:scene:20",),
            ),
        ),
        checkpoint_id="checkpoint:twenty",
        step=20,
    )

    fork = WikiStore(root, "alternate")
    fork.fork_from(
        parent_branch_id="main",
        checkpoint_id="checkpoint:ten",
        checkpoint_step=10,
    )

    content = fork.load_page("world/state.md").content
    assert "rings once" in content
    assert "rings twice" not in content
    assert fork.view().checkpoint_id == "checkpoint:ten"


def test_one_thousand_scene_updates_keep_context_bounded(tmp_path: Path) -> None:
    root = _root(tmp_path)
    store = WikiStore(root, "main")
    for step in range(1, 1_001):
        store.apply_patches(
            (
                WikiPatch(
                    path="world/threads.md",
                    section="Open Threads",
                    operation=WikiPatchOperation.REPLACE_SECTION,
                    content=f"Current bounded thread at scene {step}.",
                    source_ids=(f"event:scene:{step}",),
                ),
            ),
            checkpoint_id=f"checkpoint:{step}",
            step=step,
        )

    context = WikiContextBuilder(root, "main", max_context_chars=2_048).world()
    page = store.load_page("world/threads.md")
    assert "scene 1000" in page.content
    assert "scene 999" not in page.content
    assert len(context) <= 2_048
    assert store.view().updated_at_step == 1_000


def test_actor_and_writer_contexts_enforce_one_total_limit(tmp_path: Path) -> None:
    root = _root(tmp_path)
    memories = tuple(
        MemoryRecord(
            record_id=f"memory:chen:{step}",
            record_type=MemoryRecordType.OBSERVATION,
            scope=MemoryScope.CHARACTER,
            owner_id="chen-mo",
            session_id="session:context",
            branch_id="main",
            step=step,
            text=f"memory {step} " + ("x" * 10_000),
            content_locale="zh-CN",
            created_at=datetime.now(UTC),
            visible_to=("chen-mo",),
        )
        for step in range(1, 9)
    )
    builder = WikiContextBuilder(root, "main", max_context_chars=1_024)

    actor = builder.actor("chen-mo", memories)
    writer = builder.writer("chen-mo")

    assert len(actor) <= 1_024
    assert "Character Wiki:" in actor
    assert "Current scene and recent raw observations:" in actor
    assert "memory 1" in actor
    assert "memory 8" in actor
    assert len(writer) <= 1_024
    assert "World Wiki:" in writer
    assert "Viewpoint Wiki:" in writer


def test_store_rejects_a_page_that_would_exceed_the_limit(tmp_path: Path) -> None:
    store = WikiStore(_root(tmp_path), "main")
    with pytest.raises(ValueError, match="page length limit"):
        store.apply_patches(
            (
                WikiPatch(
                    path="world/state.md",
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content="x" * 65_536,
                    source_ids=("event:too-large",),
                ),
            ),
            checkpoint_id="checkpoint:large",
            step=1,
        )
