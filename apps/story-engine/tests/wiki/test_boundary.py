import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.projection import (
    EventVisibility,
    ResolvedEvent,
    ResolvedTurn,
    SimulationBoundary,
)
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
from story_engine.persistence.commit import SimulationCommitKernel
from story_engine.persistence.simulation_log import SimulationLogRecord
from story_engine.simulation.session import calculate_snapshot_state_hash
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.wiki.boundary import WikiBoundaryProcessor
from story_engine.wiki.store import WikiRevisionConflictError, WikiStore
from story_engine.workspace.project_store import ProjectStore


def _snapshot(tmp_path: Path) -> TurnSessionSnapshot:
    now = datetime.now(UTC)
    request = TurnSessionRequest(
        project_id="fog-harbor",
        branch_id="main",
        premise_text="The lighthouse goes dark.",
        actor_ids=("chen-mo",),
        content_locale="zh-CN",
        control=ControlPolicy(mode=ControlMode.STEP),
    )
    return TurnSessionSnapshot(
        session_id="session:1",
        project_id=request.project_id,
        branch_id=request.branch_id,
        status=TurnSessionStatus.PAUSED,
        content_locale=request.content_locale,
        request=request,
        roster_actor_ids=("chen-mo",),
        characters=ProjectStore(tmp_path / "fog-harbor").load().characters,
        current_step=1,
        actor_states={"chen-mo": {}},
        game_master_states={"gm": {}},
        raw_log_offset=1,
        checkpoint_id="checkpoint:test",
        started_at=now,
        updated_at=now,
        state_hash="0" * 64,
    )


def _record(step: int) -> SimulationLogRecord:
    now = datetime.now(UTC)
    return SimulationLogRecord.create(
        record_kind="turn",
        parent_log_id="log-" + "0" * 64,
        result=StepResult(
            session_id="session:1",
            branch_id="main",
            step=step,
            acting_actor_id="chen-mo",
            action_spec=None,
            action_text="Inspect the mechanism.",
            resolved_turn=None,
            status=TurnSessionStatus.PAUSED,
            boundary=SimulationBoundary.SCENE,
        ),
        trace=TurnTrace(
            trace_id=f"trace:session-1:{step}",
            session_id="session:1",
            branch_id="main",
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


def _record_with_memory(
    step: int,
    memory: MemoryRecord,
) -> SimulationLogRecord:
    record = _record(step)
    return SimulationLogRecord.create(
        record_kind="turn",
        parent_log_id=record.parent_log_id,
        result=record.result,
        trace=record.trace,
        memory_delta=(memory,),
    )


def _record_with_event(step: int) -> SimulationLogRecord:
    record = _record(step)
    event_id = f"event:session:1:{step}"
    resolved = ResolvedTurn(
        session_id="session:1",
        branch_id="main",
        step=step,
        acting_actor_id="chen-mo",
        putative_event_text="Inspect the mechanism.",
        raw_resolution_text="The mechanism clicks.",
        events=(
            ResolvedEvent(
                event_id=event_id,
                session_id="session:1",
                step=step,
                actor_id="chen-mo",
                event_text="The mechanism clicks.",
                visibility=EventVisibility.PUBLIC,
                content_locale="en-US",
                occurred_at=datetime.now(UTC),
            ),
        ),
        boundary=SimulationBoundary.SCENE,
        content_locale="en-US",
    )
    return SimulationLogRecord.create(
        record_kind="turn",
        parent_log_id=record.parent_log_id,
        result=record.result.model_copy(update={"resolved_turn": resolved}),
        trace=record.trace,
        memory_delta=record.memory_delta,
    )


class RetryingConsolidator:
    def __init__(self) -> None:
        self.calls = 0

    async def consolidate(
        self,
        *,
        project_id: str,
        session_id: str | None,
        step: int,
        branch_id: str,
        subject_id: str | None,
        pages: tuple,
        sources: tuple,
        content_locale: str,
    ) -> tuple[WikiPatch, ...]:
        del (
            project_id,
            session_id,
            step,
            branch_id,
            subject_id,
            pages,
            sources,
            content_locale,
        )
        self.calls += 1
        return (
            WikiPatch(
                path="world/state.md",
                operation=WikiPatchOperation.APPEND_HISTORY,
                content="## Scene\n\nRetried update.",
                source_ids=("project:fog-harbor",),
            ),
        )


def test_boundary_retries_once_on_revision_conflict(
    tmp_path: Path,
    monkeypatch,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    store = WikiStore(tmp_path / "fog-harbor", "main")
    original = WikiStore.apply_patches
    attempts = 0

    def conflicting_apply(
        self,
        patches,
        *,
        checkpoint_id: str,
        step: int,
        precondition=None,
    ):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise WikiRevisionConflictError("world/state.md", 0)
        return original(
            self,
            patches,
            checkpoint_id=checkpoint_id,
            step=step,
            precondition=precondition,
        )

    monkeypatch.setattr(WikiStore, "apply_patches", conflicting_apply)
    consolidator = RetryingConsolidator()
    processor = WikiBoundaryProcessor(
        tmp_path / "fog-harbor",
        consolidator=consolidator,
    )

    written = asyncio.run(
        processor._process_with_sources(
            _snapshot(tmp_path),
            boundary=SimulationBoundary.SCENE,
            end_step=1,
            records=(_record(1),),
        )
    )

    assert attempts == 2
    assert consolidator.calls == 2
    assert written
    page = store.load_page("world/state.md")
    assert "Retried update" in page.content
    assert page.revision == 1


class CurrentSourceConsolidator:
    async def consolidate(
        self,
        *,
        project_id: str,
        session_id: str | None,
        step: int,
        branch_id: str,
        subject_id: str | None,
        pages: tuple,
        sources: tuple,
        content_locale: str,
    ) -> tuple[WikiPatch, ...]:
        del project_id, session_id, step, branch_id, pages, content_locale
        if subject_id is not None:
            return ()
        source_ids = {source.source_id for source in sources}
        assert "event:session:1:0" not in source_ids
        current_event_id = "event:session:1:1"
        assert current_event_id in source_ids
        return (
            WikiPatch(
                path="world/state.md",
                operation=WikiPatchOperation.APPEND_HISTORY,
                content="## Follow-up\n\nThe current event is recorded.",
                source_ids=(current_event_id,),
            ),
        )


def test_boundary_does_not_carry_page_sources_into_later_scene(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    store = WikiStore(tmp_path / "fog-harbor", "main")
    current = store.load_page("world/state.md")
    store.apply_patches(
        (
            WikiPatch(
                path="world/state.md",
                operation=WikiPatchOperation.APPEND_HISTORY,
                content="## Earlier scene\n\nThe mechanism was first observed.",
                source_ids=("event:session:1:0",),
                expected_revision=current.revision,
                expected_content_hash=current.content_hash,
            ),
        ),
        checkpoint_id="checkpoint:step-0",
        step=0,
    )
    processor = WikiBoundaryProcessor(
        tmp_path / "fog-harbor",
        consolidator=CurrentSourceConsolidator(),
    )

    written = asyncio.run(
        processor._process_with_sources(
            _snapshot(tmp_path),
            boundary=SimulationBoundary.SCENE,
            end_step=1,
            records=(_record_with_event(0), _record_with_event(1)),
        )
    )

    assert written
    assert "current event is recorded" in store.load_page("world/state.md").content


class ProjectSourceConsolidator:
    async def consolidate(
        self,
        *,
        project_id: str,
        session_id: str | None,
        step: int,
        branch_id: str,
        subject_id: str | None,
        pages: tuple,
        sources: tuple,
        content_locale: str,
    ) -> tuple[WikiPatch, ...]:
        del project_id, session_id, step, branch_id, pages, content_locale
        if subject_id is None:
            return ()
        project_source_id = "project:fog-harbor"
        assert project_source_id in {source.source_id for source in sources}
        return (
            WikiPatch(
                path=f"characters/{subject_id}/self.md",
                operation=WikiPatchOperation.APPEND_HISTORY,
                content="## Project context\n\nThe public premise still applies.",
                source_ids=(project_source_id,),
            ),
        )


def test_boundary_keeps_project_source_available_to_character_wiki(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    observation = MemoryRecord(
        record_id="observation:session:1:1:chen-mo",
        record_type=MemoryRecordType.OBSERVATION,
        scope=MemoryScope.CHARACTER,
        owner_id="chen-mo",
        session_id="session:1",
        branch_id="main",
        step=1,
        text="The mechanism clicks.",
        content_locale="en-US",
        created_at=datetime.now(UTC),
        visible_to=("chen-mo",),
    )

    processor = WikiBoundaryProcessor(
        tmp_path / "fog-harbor",
        consolidator=ProjectSourceConsolidator(),
    )

    written = asyncio.run(
        processor._process_with_sources(
            _snapshot(tmp_path),
            boundary=SimulationBoundary.SCENE,
            end_step=1,
            records=(_record_with_memory(1, observation),),
        )
    )

    assert written
    assert (
        "public premise still applies"
        in WikiStore(tmp_path / "fog-harbor", "main")
        .load_page("characters/chen-mo/self.md")
        .content
    )


class CrossBoundaryConsolidator:
    async def consolidate(
        self,
        *,
        project_id: str,
        session_id: str | None,
        step: int,
        branch_id: str,
        subject_id: str | None,
        pages: tuple,
        sources: tuple,
        content_locale: str,
    ) -> tuple[WikiPatch, ...]:
        del project_id, session_id, step, branch_id, pages, sources, content_locale
        if subject_id is None:
            return ()
        return (
            WikiPatch(
                path="characters/lin-lan/beliefs.md",
                operation=WikiPatchOperation.APPEND_HISTORY,
                content="- Knowledge from another character.",
                source_ids=("project:fog-harbor",),
            ),
        )


def test_boundary_does_not_degrade_or_swallow_knowledge_violation(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    observation = MemoryRecord(
        record_id="observation:session:1:1:chen-mo",
        record_type=MemoryRecordType.OBSERVATION,
        scope=MemoryScope.CHARACTER,
        owner_id="chen-mo",
        session_id="session:1",
        branch_id="main",
        step=1,
        text="The mechanism clicks.",
        content_locale="en-US",
        created_at=datetime.now(UTC),
        visible_to=("chen-mo",),
    )

    processor = WikiBoundaryProcessor(
        tmp_path / "fog-harbor",
        consolidator=CrossBoundaryConsolidator(),
    )

    with pytest.raises(ValueError, match="crosses its knowledge boundary"):
        asyncio.run(
            processor._process_with_sources(
                _snapshot(tmp_path),
                boundary=SimulationBoundary.SCENE,
                end_step=1,
                records=(_record_with_memory(1, observation),),
            )
        )


class CapturingSourceConsolidator:
    def __init__(self) -> None:
        self.sources: dict[str, set[str]] = {}

    async def consolidate(
        self,
        *,
        project_id: str,
        session_id: str | None,
        step: int,
        branch_id: str,
        subject_id: str | None,
        pages: tuple,
        sources: tuple,
        content_locale: str,
    ) -> tuple[WikiPatch, ...]:
        del project_id, session_id, step, branch_id, pages, content_locale
        self.sources.setdefault(subject_id or "world", set()).update(
            source.source_id for source in sources
        )
        return ()


def test_rollback_restores_reachable_wiki_and_removes_abandoned_content(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    root = tmp_path / "fog-harbor"
    kernel = SimulationCommitKernel(root)

    def snapshot(step: int, history_head_id: str | None = None):
        provisional = _snapshot(tmp_path).model_copy(
            update={
                "current_step": step,
                "raw_log_offset": step,
                "checkpoint_id": None,
                "history_head_id": history_head_id,
                "state_hash": "0" * 64,
            }
        )
        return provisional.model_copy(
            update={"state_hash": calculate_snapshot_state_hash(provisional)}
        )

    def memory(
        record_id: str,
        *,
        step: int,
        text: str,
        owner_id: str,
        scope: MemoryScope,
        record_type: MemoryRecordType,
        tags: tuple[str, ...],
    ) -> MemoryRecord:
        return MemoryRecord(
            record_id=record_id,
            record_type=record_type,
            scope=scope,
            owner_id=owner_id,
            session_id="session:1",
            branch_id="main",
            step=step,
            text=text,
            content_locale="zh-CN",
            created_at=datetime.now(UTC),
            visible_to=((owner_id,) if scope == MemoryScope.CHARACTER else ()),
            tags=tags,
        )

    initial_instruction = memory(
        "instruction:initial",
        step=0,
        text="Keep the lighthouse uncertain.",
        owner_id="gm",
        scope=MemoryScope.GAME_MASTER,
        record_type=MemoryRecordType.SYSTEM,
        tags=("director_instruction",),
    )
    initial = kernel.save_checkpoint(
        snapshot(0),
        reason="Genesis",
        genesis_memory_delta=(initial_instruction,),
    )
    reachable_observation = memory(
        "observation:reachable",
        step=0,
        text="The reachable mechanism clicks.",
        owner_id="chen-mo",
        scope=MemoryScope.CHARACTER,
        record_type=MemoryRecordType.OBSERVATION,
        tags=("observation",),
    )
    first_record = _record(0)
    checkpoint_one = kernel.append_step(
        first_record.result,
        snapshot(1, initial.history_head_id),
        first_record.trace,
        memory_delta=(reachable_observation,),
    )
    store = WikiStore(root, "main")
    reachable_page = store.load_page("world/state.md")
    store.apply_patches(
        (
            WikiPatch(
                path="world/state.md",
                operation=WikiPatchOperation.APPEND_HISTORY,
                content="## Reachable\n\nThe reachable mechanism clicks.",
                source_ids=("observation:reachable",),
                expected_revision=reachable_page.revision,
                expected_content_hash=reachable_page.content_hash,
            ),
        ),
        checkpoint_id=checkpoint_one.checkpoint_id,
        step=0,
    )
    abandoned_observation = memory(
        "observation:abandoned",
        step=1,
        text="This observation belongs to the abandoned timeline.",
        owner_id="chen-mo",
        scope=MemoryScope.CHARACTER,
        record_type=MemoryRecordType.OBSERVATION,
        tags=("observation",),
    )
    future_instruction = memory(
        "instruction:future",
        step=1,
        text="Reveal the future mechanism.",
        owner_id="gm",
        scope=MemoryScope.GAME_MASTER,
        record_type=MemoryRecordType.SYSTEM,
        tags=("director_instruction",),
    )
    second_record = _record(1)
    checkpoint_two = kernel.append_step(
        second_record.result,
        snapshot(2, checkpoint_one.history_head_id),
        second_record.trace,
        memory_delta=(abandoned_observation, future_instruction),
    )
    abandoned_page = store.load_page("world/state.md")
    store.apply_patches(
        (
            WikiPatch(
                path="world/state.md",
                operation=WikiPatchOperation.APPEND_HISTORY,
                content="## Abandoned\n\nABANDONED_TIMELINE_CONTENT",
                source_ids=("observation:abandoned",),
                expected_revision=abandoned_page.revision,
                expected_content_hash=abandoned_page.content_hash,
            ),
            WikiPatch(
                path="world/abandoned.md",
                operation=WikiPatchOperation.CREATE,
                content="# Abandoned\n\nABANDONED_ONLY_PAGE",
                source_ids=("observation:abandoned",),
            ),
        ),
        checkpoint_id=checkpoint_two.checkpoint_id,
        step=1,
    )
    kernel.rollback_branch(
        "fog-harbor",
        "main",
        checkpoint_id=checkpoint_one.checkpoint_id,
    )
    restored = kernel.load_checkpoint("fog-harbor", checkpoint_one.checkpoint_id)
    asyncio.run(
        WikiBoundaryProcessor(
            root,
            consolidator=CapturingSourceConsolidator(),
        ).rebuild(restored)
    )

    content = store.load_page("world/state.md").content
    assert "reachable mechanism clicks" in content
    assert "ABANDONED_TIMELINE_CONTENT" not in content
    assert not store.page_path("world/abandoned.md").exists()
    assert store.view().checkpoint_id == checkpoint_one.checkpoint_id
