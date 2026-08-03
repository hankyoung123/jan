from datetime import datetime

from pydantic import Field, model_validator

from story_engine.domain.base import Identifier, RuntimeModel
from story_engine.domain.projection import SimulationBoundary
from story_engine.domain.simulation import (
    MaintenanceStatus,
    TurnSessionSnapshot,
    TurnSessionStatus,
)


class SessionManifest(RuntimeModel):
    session_id: Identifier
    project_id: Identifier
    branch_id: Identifier
    status: TurnSessionStatus
    current_step: int = Field(ge=0)
    completed_scenes: int = Field(ge=0)
    head_checkpoint_id: Identifier | None
    resolved_model_profile_ids: dict[Identifier, Identifier] = Field(
        default_factory=dict
    )
    started_at: datetime
    updated_at: datetime
    termination_reason_text: str | None = None
    restoration_notice_text: str | None = None
    maintenance_status: MaintenanceStatus = MaintenanceStatus.NOT_REQUIRED
    maintenance_error_text: str | None = None
    maintenance_step: int | None = Field(default=None, ge=0)
    maintenance_boundary: SimulationBoundary = SimulationBoundary.NONE

    @model_validator(mode="after")
    def timestamps_are_valid(self) -> "SessionManifest":
        if self.started_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("session manifest timestamps require timezones")
        if self.updated_at < self.started_at:
            raise ValueError("session manifest update precedes its start")
        return self

    @classmethod
    def from_snapshot(
        cls,
        snapshot: TurnSessionSnapshot,
        *,
        status: TurnSessionStatus | None = None,
        restoration_notice_text: str | None = None,
    ) -> "SessionManifest":
        return cls(
            session_id=snapshot.session_id,
            project_id=snapshot.project_id,
            branch_id=snapshot.branch_id,
            status=status or snapshot.status,
            current_step=snapshot.current_step,
            completed_scenes=snapshot.completed_scenes,
            head_checkpoint_id=snapshot.checkpoint_id,
            resolved_model_profile_ids=snapshot.resolved_model_profile_ids,
            started_at=snapshot.started_at,
            updated_at=snapshot.updated_at,
            termination_reason_text=snapshot.termination_reason_text,
            restoration_notice_text=(
                restoration_notice_text or snapshot.restoration_notice_text
            ),
            maintenance_status=snapshot.maintenance_status,
            maintenance_error_text=snapshot.maintenance_error_text,
            maintenance_step=snapshot.maintenance_step,
            maintenance_boundary=snapshot.maintenance_boundary,
        )
