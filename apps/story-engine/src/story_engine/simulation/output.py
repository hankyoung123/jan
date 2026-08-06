import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

from story_engine.domain.projection import SimulationBoundary
from story_engine.domain.simulation import (
    ManuscriptGenerationMode,
    StepResult,
    TurnSessionSnapshot,
    WikiMaintenanceMode,
)
from story_engine.manuscript.service import ManuscriptAgent, ManuscriptService
from story_engine.wiki.boundary import WikiBoundaryProcessor
from story_engine.wiki.consolidator import WikiProtocolError
from story_engine.wiki.store import WikiStore
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope
from story_engine.workspace.lock import ProjectLock


class BoundaryMaintenanceCoordinator:
    """Maintain Wiki and optional prose after the durable runtime commit."""

    def __init__(
        self,
        root: Path,
        *,
        wiki_processor: WikiBoundaryProcessor,
        manuscript_agent: ManuscriptAgent,
    ) -> None:
        self.root = root
        self.wiki_processor = wiki_processor
        self.manuscript_agent = manuscript_agent

    def requires_wiki(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> bool:
        return result.boundary != SimulationBoundary.NONE and self._should_update_wiki(
            snapshot,
            result.boundary,
        )

    def process_wiki(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> str | None:
        if snapshot.checkpoint_id is None:
            raise WikiMaintenanceError("boundary has no checkpoint")
        try:
            asyncio.run(
                self.wiki_processor.process(
                    snapshot,
                    boundary=result.boundary,
                    end_step=result.step,
                )
            )
        except WikiProtocolError as error:
            detail = str(error)
            try:
                WikiStore(self.root, snapshot.branch_id).mark_degraded(
                    snapshot.checkpoint_id,
                    result.step,
                    detail,
                )
                self._record_failure(snapshot, result, f"wiki degraded: {detail}")
            except Exception as persistence_error:
                raise WikiMaintenanceError(
                    str(persistence_error)
                ) from persistence_error
            return detail
        except Exception as error:
            try:
                self._record_failure(snapshot, result, f"wiki: {error}")
            except Exception as persistence_error:
                raise WikiMaintenanceError(
                    str(persistence_error)
                ) from persistence_error
            raise WikiMaintenanceError(str(error)) from error
        return None

    def process(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> str | None:
        if result.boundary == SimulationBoundary.NONE:
            return None
        if snapshot.checkpoint_id is None:
            self._record_failure(snapshot, result, "boundary has no checkpoint")
            raise WikiMaintenanceError("boundary has no checkpoint")

        if self._should_update_wiki(snapshot, result.boundary):
            degradation_reason = self.process_wiki(result, snapshot)
            if degradation_reason is not None:
                return degradation_reason

        wiki = WikiStore(self.root, snapshot.branch_id).view()
        if wiki.stale:
            return wiki.degradation_reason or "Wiki is stale and requires rebuilding"

        if self._should_write(snapshot, result.boundary):
            try:
                service = ManuscriptService(
                    self.root,
                    snapshot.branch_id,
                    agent=self.manuscript_agent,
                )
                source = next(
                    (
                        item
                        for item in reversed(service.list_sources())
                        if item.to_step == result.step and item.status == "available"
                    ),
                    None,
                )
                if source is not None:
                    asyncio.run(
                        service.generate_scene(
                            checkpoint_id=snapshot.checkpoint_id,
                            from_step=source.from_step,
                            to_step=source.to_step,
                            chapter_id=(
                                f"chapter-{max(1, snapshot.completed_scenes):03d}"
                            ),
                            viewpoint_actor_id=None,
                        )
                    )
            except Exception as error:
                self._record_failure(snapshot, result, f"manuscript: {error}")
        return None

    @staticmethod
    def _should_update_wiki(
        snapshot: TurnSessionSnapshot,
        boundary: SimulationBoundary,
    ) -> bool:
        mode = snapshot.request.output.wiki_mode
        return mode == WikiMaintenanceMode.AFTER_SCENE or (
            mode == WikiMaintenanceMode.AFTER_CHAPTER
            and boundary == SimulationBoundary.CHAPTER
        )

    @staticmethod
    def _should_write(
        snapshot: TurnSessionSnapshot,
        boundary: SimulationBoundary,
    ) -> bool:
        mode = snapshot.request.output.manuscript_mode
        return mode == ManuscriptGenerationMode.AFTER_SCENE or (
            mode == ManuscriptGenerationMode.AFTER_CHAPTER
            and boundary == SimulationBoundary.CHAPTER
        )

    def _record_failure(
        self,
        snapshot: TurnSessionSnapshot,
        result: StepResult,
        detail: str,
    ) -> None:
        recorded_at = datetime.now(UTC)
        failure_id = f"failure-{uuid.uuid4().hex}"
        path = self.root / ".story-engine/runtime/output-failures" / f"{failure_id}.md"
        payload = {
            "failure_id": failure_id,
            "project_id": snapshot.project_id,
            "branch_id": snapshot.branch_id,
            "session_id": snapshot.session_id,
            "checkpoint_id": snapshot.checkpoint_id,
            "step": result.step,
            "boundary": result.boundary.value,
            "detail": detail,
            "recorded_at": recorded_at.isoformat(),
        }
        content = dump_json_envelope(
            schema="story-engine/output-failure/v1",
            title=f"Output Failure {failure_id}",
            metadata={
                "failure_id": failure_id,
                "branch_id": snapshot.branch_id,
                "step": result.step,
            },
            body=f"# Output Failure\n\n{detail}",
            payload=payload,
        )
        with ProjectLock(self.root):
            atomic_write_text(path, content, overwrite=False)


class WikiMaintenanceError(RuntimeError):
    """Non-protocol Wiki failure that must block the next simulation step."""
