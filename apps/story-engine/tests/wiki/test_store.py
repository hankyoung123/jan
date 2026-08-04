from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.wiki import WikiPatch, WikiPatchOperation
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.wiki.context import WikiContextBuilder
from story_engine.wiki.store import WikiRevisionConflictError, WikiStore


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
                expected_revision=0,
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
                expected_revision=1,
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
                    expected_revision=step - 1,
                ),
            ),
            checkpoint_id=f"checkpoint:{step}",
            step=step,
        )

    context = WikiContextBuilder(root, "main", max_context_chars=2_048).world()
    page = store.load_page("world/threads.md")
    assert "scene 1000" in page.content
    assert "scene 999" not in page.content
    assert len(context.content) <= 2_048
    assert context.manifest
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

    assert len(actor.content) <= 1_024
    assert "Character Wiki:" in actor.content
    assert "Current scene and recent raw observations:" in actor.content
    assert "memory 1" in actor.content
    assert "memory 8" in actor.content
    assert any(item.reason == "current_observation" for item in actor.manifest)
    assert len(writer.content) <= 1_024
    assert "World Wiki:" in writer.content
    assert "Viewpoint Wiki:" in writer.content


def test_context_routes_relationship_and_location_before_hundreds_of_pages(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    store = WikiStore(root, "main")
    for index in range(250):
        store.apply_patches(
            (
                WikiPatch(
                    path=f"world/archive/a-{index:03d}.md",
                    operation=WikiPatchOperation.CREATE,
                    content=f"# Archived {index}\n\nUnrelated old material.",
                    source_ids=(f"event:archive:{index}",),
                ),
            ),
            checkpoint_id=f"checkpoint:archive:{index}",
            step=index + 1,
        )
    store.apply_patches(
        (
            WikiPatch(
                path="world/relationships/chen-mo-lin-lan.md",
                operation=WikiPatchOperation.CREATE,
                content="# Relationship\n\nchen-mo trusts lin-lan at the signal tower.",
                source_ids=("event:relationship:current",),
            ),
            WikiPatch(
                path="world/locations/signal-tower.md",
                operation=WikiPatchOperation.CREATE,
                content="# Signal Tower\n\nThe current confrontation is here.",
                source_ids=("event:location:current",),
            ),
        ),
        checkpoint_id="checkpoint:current",
        step=999,
    )

    context = WikiContextBuilder(root, "main", max_context_chars=2_048).world(
        participant_ids=("chen-mo", "lin-lan"),
        location_ids=("signal-tower",),
    )

    selected = {item.path: item.reason for item in context.manifest}
    assert selected["world/relationships/chen-mo-lin-lan.md"] == (
        "current_participant_relationship"
    )
    assert selected["world/locations/signal-tower.md"] == "current_location"
    assert "world/archive/a-000.md" not in selected


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
                    expected_revision=0,
                ),
            ),
            checkpoint_id="checkpoint:large",
            step=1,
        )


def test_stale_revision_patch_is_rejected_without_partial_writes(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    store = WikiStore(root, "main")
    before = store.load_page("world/state.md")
    rules_before = store.load_page("world/rules.md")

    with pytest.raises(WikiRevisionConflictError, match="current revision 0"):
        store.apply_patches(
            (
                WikiPatch(
                    path="world/state.md",
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content="## Scene 1\n\nFirst writer.",
                    source_ids=("event:scene:1",),
                    expected_revision=0,
                ),
                WikiPatch(
                    path="world/rules.md",
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content="## Rule\n\nSecond writer.",
                    source_ids=("event:scene:1",),
                    expected_revision=1,
                ),
            ),
            checkpoint_id="checkpoint:one",
            step=1,
        )

    after = store.load_page("world/state.md")
    rules = store.load_page("world/rules.md")
    assert after.revision == before.revision == 0
    assert after.content == before.content
    assert rules.revision == rules_before.revision == 0
    assert rules.content == rules_before.content
    log = (root / "wiki/branches/main/log.md").read_text(encoding="utf-8")
    assert "First writer" not in log


def test_content_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    store = WikiStore(_root(tmp_path), "main")

    with pytest.raises(WikiRevisionConflictError):
        store.apply_patches(
            (
                WikiPatch(
                    path="world/state.md",
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content="## Scene 1\n\nStale base.",
                    source_ids=("event:scene:1",),
                    expected_revision=0,
                    expected_content_hash="0" * 64,
                ),
            ),
            checkpoint_id="checkpoint:one",
            step=1,
        )


def test_save_page_bumps_revision_and_rejects_stale_edits(tmp_path: Path) -> None:
    store = WikiStore(_root(tmp_path), "main")

    updated = store.save_page(
        "world/state.md",
        "# Current World State\n\nEdited by hand.",
        expected_revision=0,
    )

    assert updated.revision == 1
    assert len(updated.content_hash) == 64
    assert "Edited by hand" in updated.content
    assert store.load_page("world/state.md").revision == 1
    with pytest.raises(WikiRevisionConflictError, match="current revision 1"):
        store.save_page(
            "world/state.md",
            "# Current World State\n\nToo late.",
            expected_revision=0,
        )


def test_create_patch_cannot_carry_expected_revision() -> None:
    with pytest.raises(ValidationError, match="create patch"):
        WikiPatch(
            path="world/new.md",
            operation=WikiPatchOperation.CREATE,
            content="# New\n\nContent.",
            source_ids=("event:new",),
            expected_revision=0,
        )
