from typing import Self

from pydantic import Field, model_validator

from story_engine.domain.models import DomainModel
from story_engine.models.contracts import ModelTask

DEFAULT_TASK_PROFILE_IDS: dict[ModelTask, str] = {
    "actor": "actor",
    "game_master": "game-master",
    "wiki_maintenance": "wiki-maintenance",
    "editor": "editor",
    "writer": "writer",
}


class ProjectModelPolicy(DomainModel):
    schema_version: int = Field(default=1, ge=1, le=1)
    task_profile_ids: dict[ModelTask, str] = Field(
        default_factory=lambda: dict(DEFAULT_TASK_PROFILE_IDS)
    )
    agent_profile_ids: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def assignments_are_complete_and_well_formed(self) -> Self:
        if set(self.task_profile_ids) != set(DEFAULT_TASK_PROFILE_IDS):
            raise ValueError("task profile assignments must contain every model task")
        assignments = (
            *self.task_profile_ids.values(),
            *self.agent_profile_ids.values(),
        )
        if any(not profile_id for profile_id in assignments):
            raise ValueError("model policy profile IDs must not be empty")
        if any(not agent_id for agent_id in self.agent_profile_ids):
            raise ValueError("model policy agent IDs must not be empty")
        return self


class ProjectModelPolicyPatch(DomainModel):
    task_profile_ids: dict[ModelTask, str] | None = None
    agent_profile_ids: dict[str, str | None] | None = None
