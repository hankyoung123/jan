from datetime import UTC, datetime
from pathlib import Path

from story_engine.domain.projection import SimulationBoundary
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import ModelCallStatus, TurnTrace
from story_engine.domain.wiki import WikiPatch, WikiPatchOperation
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.simulation_log import (
    SimulationLogRecord,
    SimulationLogStore,
)
from story_engine.simulation.session import calculate_snapshot_state_hash
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.wiki.lint import WikiLinter
from story_engine.wiki.store import WikiStore


def _snapshot(tmp_path: Path, *, step: int = 5) -> TurnSessionSnapshot:
    now = datetime.now(UTC)
    request = TurnSessionRequest(
        project_id="fog-harbor",
        branch_id="main",
        premise_text="The lighthouse goes dark.",
        actor_ids=("chen-mo",),
        content_locale="zh-CN",
        control=ControlPolicy(mode=ControlMode.STEP),
    )
    provisional = TurnSessionSnapshot(
        session_id="session:lint",
        project_id=request.project_id,
        branch_id=request.branch_id,
        status=TurnSessionStatus.PAUSED,
        content_locale=request.content_locale,
        request=request,
        current_step=step,
        actor_states={"chen-mo": {}},
        game_master_states={"gm": {}},
        raw_log_offset=step,
        started_at=now,
        updated_at=now,
        state_hash="0" * 64,
    )
    return provisional.model_copy(
        update={"state_hash": calculate_snapshot_state_hash(provisional)}
    )


def _record(step: int, *, branch_id: str, session_id: str) -> SimulationLogRecord:
    now = datetime.now(UTC)
    return SimulationLogRecord.create(
        record_kind="turn",
        parent_log_id="log-" + "0" * 64,
        result=StepResult(
            session_id=session_id,
            branch_id=branch_id,
            step=step,
            acting_actor_id="chen-mo",
            action_spec=None,
            action_text="Inspect the mechanism.",
            resolved_turn=None,
            status=TurnSessionStatus.PAUSED,
            boundary=SimulationBoundary.NONE,
        ),
        trace=TurnTrace(
            trace_id=f"trace:{session_id}:{step}",
            session_id=session_id,
            branch_id=branch_id,
            step=step,
            content_locale="zh-CN",
            stages=(),
            model_calls=(),
            acting_actor_id="chen-mo",
            started_at=now,
            completed_at=now,
            status=ModelCallStatus.SUCCEEDED,
        ),
        memory_delta=(),
    )


def _root(tmp_path: Path) -> Path:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    return tmp_path / "fog-harbor"


def test_lint_reports_missing_source(tmp_path: Path) -> None:
    store = WikiStore(_root(tmp_path), "main")
    store.apply_patches(
        (
            WikiPatch(
                path="world/extra.md",
                operation=WikiPatchOperation.CREATE,
                content="# Extra\n\nUnverified material.",
                source_ids=("event:missing",),
            ),
        ),
        checkpoint_id="checkpoint:lint",
        step=1,
    )

    result = WikiLinter(tmp_path / "fog-harbor", "main").run()

    assert not result.passed
    assert any(issue.code == "source-not-found" for issue in result.issues)


def test_lint_rejects_private_and_mismatched_sources(tmp_path: Path) -> None:
    store = WikiStore(_root(tmp_path), "main")
    store.apply_patches(
        (
            WikiPatch(
                path="world/leak.md",
                operation=WikiPatchOperation.CREATE,
                content="# Leak\n\nPrivate material.",
                source_ids=("profile:chen-mo",),
            ),
            WikiPatch(
                path="characters/chen-mo/mismatch.md",
                operation=WikiPatchOperation.CREATE,
                content="# Mismatch\n\nWrong character.",
                source_ids=("profile:lin-lan",),
            ),
        ),
        checkpoint_id="checkpoint:lint",
        step=1,
    )

    result = WikiLinter(tmp_path / "fog-harbor", "main").run()

    codes = {issue.code for issue in result.issues}
    assert "private-source-leak" in codes
    assert "source-subject-mismatch" in codes


def test_lint_rejects_unreachable_physical_history_sources(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    logs = SimulationLogStore(root)
    logs.append(
        "alternate",
        _record(3, branch_id="alternate", session_id="session:alt"),
    )
    logs.append(
        "main",
        _record(5, branch_id="main", session_id="session:main"),
    )
    store = WikiStore(root, "main")
    store.apply_patches(
        (
            WikiPatch(
                path="world/alt.md",
                operation=WikiPatchOperation.CREATE,
                content="# Alt\n\nFrom another branch.",
                source_ids=("action:session:alt:3",),
            ),
            WikiPatch(
                path="world/late.md",
                operation=WikiPatchOperation.CREATE,
                content="# Late\n\nFuture knowledge.",
                source_ids=("action:session:main:5",),
            ),
        ),
        checkpoint_id="checkpoint:lint",
        step=1,
    )

    result = WikiLinter(root, "main").run()

    source_issues = tuple(
        issue
        for issue in result.issues
        if issue.path in {"world/alt.md", "world/late.md"}
    )
    assert {issue.code for issue in source_issues} == {"source-not-found"}


def test_lint_uses_reachable_projection_validity(tmp_path: Path) -> None:
    root = _root(tmp_path)
    checkpoints = CheckpointStore(root)
    seed_checkpoint, _ = checkpoints.save(_snapshot(tmp_path, step=0))
    wiki_checkpoint, _ = checkpoints.save(
        _snapshot(tmp_path, step=3),
        parent_checkpoint_id=seed_checkpoint,
    )
    head_checkpoint, _ = checkpoints.save(
        _snapshot(tmp_path, step=5),
        parent_checkpoint_id=wiki_checkpoint,
    )
    branches = BranchStore(root)
    branches.ensure(
        branch_id="main",
        project_id="fog-harbor",
        content_locale="zh-CN",
    )
    branches.advance(
        "main",
        checkpoint_id=head_checkpoint,
        step=5,
        expected_head_checkpoint_id=None,
    )
    store = WikiStore(root, "main")

    seed = WikiLinter(root, "main").run()
    assert not any(
        issue.code == "wiki-projection-unavailable" for issue in seed.issues
    )

    store.set_head(wiki_checkpoint, 3, stale=False)
    ancestor = WikiLinter(root, "main").run()
    assert not any(
        issue.code == "wiki-projection-unavailable" for issue in ancestor.issues
    )

    store.set_head(wiki_checkpoint, 3, stale=True)
    stale = WikiLinter(root, "main").run()
    assert any(
        issue.code == "wiki-projection-unavailable" for issue in stale.issues
    )

    abandoned_checkpoint, _ = checkpoints.save(
        _snapshot(tmp_path, step=4),
        parent_checkpoint_id=seed_checkpoint,
    )
    store.set_head(abandoned_checkpoint, 4, stale=False)
    abandoned = WikiLinter(root, "main").run()
    assert any(
        issue.code == "wiki-projection-unavailable" for issue in abandoned.issues
    )


def test_lint_detects_duplicate_relationship_direction(tmp_path: Path) -> None:
    store = WikiStore(_root(tmp_path), "main")
    store.apply_patches(
        (
            WikiPatch(
                path="world/relationships/chen-mo-lin-lan.md",
                operation=WikiPatchOperation.CREATE,
                content="# Relationship\n\nchen-mo trusts lin-lan.",
                source_ids=("event:rel:1",),
            ),
            WikiPatch(
                path="world/relationships/lin-lan-chen-mo.md",
                operation=WikiPatchOperation.CREATE,
                content="# Relationship\n\nlin-lan trusts chen-mo.",
                source_ids=("event:rel:2",),
            ),
        ),
        checkpoint_id="checkpoint:lint",
        step=1,
    )

    result = WikiLinter(tmp_path / "fog-harbor", "main").run()

    assert any(
        issue.code == "duplicate-relationship-direction"
        for issue in result.issues
    )
