import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from story_engine.domain.models import StoryEvent
from story_engine.events.commit import VersionConflictError
from story_engine.manuscript.models import (
    ManuscriptReviewOutput,
    SceneUpdateRequest,
    WriterOutput,
)
from story_engine.manuscript.service import ManuscriptService
from story_engine.rag.models import RetrievalEvidence
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.scene_store import SceneStore


class DeterministicManuscriptAgent:
    def __init__(self) -> None:
        self.generated_from: list[tuple[str, ...]] = []
        self.generated_evidence: list[tuple[RetrievalEvidence, ...]] = []
        self.reviewed_bodies: list[str] = []
        self.reviewed_evidence: list[tuple[RetrievalEvidence, ...]] = []

    async def generate(
        self,
        events: tuple[StoryEvent, ...],
        *,
        project_title: str,
        genre: str,
        theme: str,
        tone: str,
        evidence: tuple[RetrievalEvidence, ...],
    ) -> WriterOutput:
        del project_title, genre, theme, tone
        self.generated_from.append(tuple(event.id for event in events))
        self.generated_evidence.append(evidence)
        return WriterOutput(
            title="灯芯槽的刮痕",
            body="陈默抵达灯塔, 并在灯芯槽上发现了新鲜刮痕。",
        )

    async def review(
        self,
        events: tuple[StoryEvent, ...],
        *,
        title: str,
        body: str,
        public_fact_ids: tuple[str, ...],
        evidence: tuple[RetrievalEvidence, ...],
    ) -> ManuscriptReviewOutput:
        del events, title, public_fact_ids
        self.reviewed_bodies.append(body)
        self.reviewed_evidence.append(evidence)
        if "黑色纤维" in body:
            return ManuscriptReviewOutput.with_new_facts(
                ("刮痕末端沾着黑色纤维",)
            )
        return ManuscriptReviewOutput.passed()


def _project(tmp_path: Path) -> Path:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    root = tmp_path / "fog-harbor"
    EventStore(root).append(
        StoryEvent(
            id="event-000001",
            sequence=1,
            occurred_at=datetime(2026, 7, 31, 4, 0, tzinfo=UTC),
            summary="陈默抵达灯塔并发现灯芯槽上的新鲜刮痕。",
            participants=("chen-mo",),
            public_results=("灯芯槽上有新鲜刮痕",),
            source_turn_id="turn-000001",
            approved_by_user=True,
        )
    )
    return root


def _formal_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*.md"))
        if ".story-engine" not in path.parts
    }


def test_writer_uses_only_confirmed_events_and_creates_derived_draft(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    agent = DeterministicManuscriptAgent()
    before = _formal_bytes(root)

    draft = asyncio.run(
        ManuscriptService(root, agent=agent).generate_scene(
            ("event-000001",),
            chapter_id="chapter-03",
        )
    )

    assert draft.id == "scene-000001"
    assert draft.status == "reviewed"
    assert draft.source_event_ids == ("event-000001",)
    assert agent.generated_from == [("event-000001",)]
    assert agent.generated_evidence[0]
    assert agent.reviewed_evidence[0]
    assert {item.task for item in draft.retrieval_evidence} == {"writer", "editor"}
    assert all(
        item.chunk_id
        and item.source_id
        and item.source_path
        and item.permission_scope
        for item in draft.retrieval_evidence
    )
    assert _formal_bytes(root) == before
    assert (root / ".story-engine/scenes/scene-000001.json").is_file()
    assert not any((root / "scenes").glob("*.md"))


def test_writer_rejects_unknown_or_unconfirmed_event_sources(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    service = ManuscriptService(root, agent=DeterministicManuscriptAgent())
    before = _formal_bytes(root)

    with pytest.raises(ValueError, match="confirmed events"):
        asyncio.run(
            service.generate_scene(("event-999999",), chapter_id="chapter-03")
        )

    assert _formal_bytes(root) == before


def test_reviewed_user_edit_saves_canonical_scene_markdown(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    service = ManuscriptService(root, agent=DeterministicManuscriptAgent())
    draft = asyncio.run(
        service.generate_scene(("event-000001",), chapter_id="chapter-03")
    )

    result = asyncio.run(
        service.update_scene(
            draft.id,
            SceneUpdateRequest(
                title="灯芯槽的刮痕",
                body="陈默抵达灯塔, 并确认灯芯槽上留有新鲜刮痕。",
                expected_revision=draft.revision,
                expected_scene_version=draft.base_scene_version,
            ),
        )
    )

    assert result.status == "saved"
    assert result.scene is not None
    assert result.scene.version == 1
    assert result.draft.base_scene_version == 1
    assert result.draft.revision == 0
    assert result.amendment is None
    saved = SceneStore(root).load("scene-000001")
    assert saved.body.endswith("新鲜刮痕。")
    markdown = next((root / "scenes").glob("*.md")).read_text(encoding="utf-8")
    assert "schema: scene/v1" in markdown
    assert "# 灯芯槽的刮痕" in markdown

    second = asyncio.run(
        service.update_scene(
            draft.id,
            SceneUpdateRequest(
                title="灯芯槽里的刮痕",
                body="陈默再次检查灯芯槽上的新鲜刮痕。",
                expected_revision=result.draft.revision,
                expected_scene_version=result.draft.base_scene_version,
            ),
        )
    )
    assert second.status == "saved"
    assert second.scene is not None
    assert second.scene.version == 2
    assert second.draft.base_scene_version == 2


def test_new_user_fact_requires_amendment_before_atomic_formal_commit(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    service = ManuscriptService(root, agent=DeterministicManuscriptAgent())
    draft = asyncio.run(
        service.generate_scene(("event-000001",), chapter_id="chapter-03")
    )
    before = _formal_bytes(root)

    result = asyncio.run(
        service.update_scene(
            draft.id,
            SceneUpdateRequest(
                title=draft.title,
                body=f"{draft.body} 刮痕末端还沾着黑色纤维。",
                expected_revision=draft.revision,
                expected_scene_version=draft.base_scene_version,
            ),
        )
    )

    assert result.status == "amendment_required"
    assert result.scene is None
    assert result.amendment is not None
    assert result.amendment.proposed_facts == ("刮痕末端沾着黑色纤维",)
    assert _formal_bytes(root) == before

    committed = service.confirm_amendment(
        result.amendment.id,
        expected_scene_id=draft.id,
    )
    snapshot = ProjectStore(root).load()

    assert committed.amendment.status == "committed"
    assert committed.scene.version == 1
    assert committed.event.public_results == ("刮痕末端沾着黑色纤维",)
    assert committed.event.approved_by_user is True
    assert committed.event.source_turn_id == result.amendment.id
    assert committed.amendment.fact_ids[0] in snapshot.world.public_fact_ids
    assert snapshot.world.version == 1
    assert SceneStore(root).load(draft.id).body.endswith("黑色纤维。")


def test_stale_scene_revision_never_writes_formal_markdown(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    service = ManuscriptService(root, agent=DeterministicManuscriptAgent())
    draft = asyncio.run(
        service.generate_scene(("event-000001",), chapter_id="chapter-03")
    )
    before = _formal_bytes(root)

    with pytest.raises(VersionConflictError, match="scene draft revision"):
        asyncio.run(
            service.update_scene(
                draft.id,
                SceneUpdateRequest(
                    title=draft.title,
                    body=draft.body,
                    expected_revision=99,
                    expected_scene_version=draft.base_scene_version,
                ),
            )
        )

    assert _formal_bytes(root) == before


def test_stale_canonical_scene_version_cannot_overwrite_saved_prose(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    service = ManuscriptService(root, agent=DeterministicManuscriptAgent())
    draft = asyncio.run(
        service.generate_scene(("event-000001",), chapter_id="chapter-03")
    )
    first = asyncio.run(
        service.update_scene(
            draft.id,
            SceneUpdateRequest(
                title=draft.title,
                body=draft.body,
                expected_revision=draft.revision,
                expected_scene_version=0,
            ),
        )
    )
    assert first.scene is not None
    before = _formal_bytes(root)
    canonical = service.get_scene(draft.id)

    with pytest.raises(VersionConflictError, match="scene version"):
        asyncio.run(
            service.update_scene(
                draft.id,
                SceneUpdateRequest(
                    title="过期标题",
                    body="过期正文",
                    expected_revision=canonical.revision,
                    expected_scene_version=0,
                ),
            )
        )

    assert canonical.base_scene_version == 1
    assert _formal_bytes(root) == before


def test_external_markdown_change_with_same_version_rejects_scene_save(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    service = ManuscriptService(root, agent=DeterministicManuscriptAgent())
    draft = asyncio.run(
        service.generate_scene(("event-000001",), chapter_id="chapter-03")
    )

    store = ProjectStore(root)
    store.save_world(
        store.load().world.model_copy(
            update={"current_location": "外部编辑器改写的码头", "version": 0}
        )
    )
    after_external_edit = _formal_bytes(root)

    with pytest.raises(
        VersionConflictError,
        match="canonical workspace changed since scene load",
    ):
        asyncio.run(
            service.update_scene(
                draft.id,
                SceneUpdateRequest(
                    title=draft.title,
                    body=draft.body,
                    expected_revision=draft.revision,
                    expected_scene_version=draft.base_scene_version,
                ),
            )
        )

    assert _formal_bytes(root) == after_external_edit
    assert not any((root / "scenes").glob("*.md"))


def test_markdown_export_contains_saved_scenes_in_sequence(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    service = ManuscriptService(root, agent=DeterministicManuscriptAgent())
    draft = asyncio.run(
        service.generate_scene(("event-000001",), chapter_id="chapter-03")
    )
    asyncio.run(
        service.update_scene(
            draft.id,
            SceneUpdateRequest(
                title=draft.title,
                body=draft.body,
                expected_revision=draft.revision,
                expected_scene_version=draft.base_scene_version,
            ),
        )
    )

    exported = service.export_markdown()

    assert exported.filename == "fog-harbor-manuscript.md"
    assert exported.markdown.startswith("# 雾港\n")
    assert "## 灯芯槽的刮痕" in exported.markdown
    assert draft.body in exported.markdown
