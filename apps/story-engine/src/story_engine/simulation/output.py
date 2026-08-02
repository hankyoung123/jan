import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from story_engine.domain.projection import SimulationBoundary
from story_engine.domain.simulation import (
    ManuscriptGenerationMode,
    StepResult,
    TurnSessionSnapshot,
    WorldProjectionMode,
)
from story_engine.manuscript.service import ManuscriptAgent, ManuscriptService
from story_engine.projection.world_bible import (
    WorldBibleProjector,
    WorldBibleStore,
    read_branch_records,
)
from story_engine.workspace.lock import ProjectLock


class BoundaryOutputCoordinator:
    """Run derived outputs after durable simulation commit without rollback."""

    def __init__(
        self,
        root: Path,
        *,
        manuscript_agent: ManuscriptAgent,
    ) -> None:
        self.root = root
        self.manuscript_agent = manuscript_agent

    def process(
        self,
        result: StepResult,
        snapshot: TurnSessionSnapshot,
    ) -> None:
        if result.boundary == SimulationBoundary.NONE:
            return
        if snapshot.checkpoint_id is None:
            self._record_failure(snapshot, result, "boundary has no checkpoint")
            return

        if self._should_project(snapshot, result.boundary):
            try:
                store = WorldBibleStore(self.root, snapshot.branch_id)
                try:
                    previous = store.load()
                except FileNotFoundError:
                    previous = None
                WorldBibleProjector(self.root).rebuild_sync(
                    snapshot,
                    read_branch_records(self.root, snapshot.branch_id),
                    previous=previous,
                )
            except Exception as error:
                self._record_failure(snapshot, result, f"world_bible: {error}")

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
                        if item.to_step == result.step and not item.already_written
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

    @staticmethod
    def _should_project(
        snapshot: TurnSessionSnapshot,
        boundary: SimulationBoundary,
    ) -> bool:
        mode = snapshot.request.output.world_projection_mode
        return mode == WorldProjectionMode.AFTER_SCENE or (
            mode == WorldProjectionMode.AFTER_CHAPTER
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
        path = self.root / ".story-engine/runtime/output-failures.jsonl"
        payload = json.dumps(
            {
                "project_id": snapshot.project_id,
                "branch_id": snapshot.branch_id,
                "session_id": snapshot.session_id,
                "checkpoint_id": snapshot.checkpoint_id,
                "step": result.step,
                "boundary": result.boundary.value,
                "detail": detail,
                "recorded_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with ProjectLock(self.root):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
