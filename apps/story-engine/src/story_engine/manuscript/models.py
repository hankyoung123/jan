from typing import Literal, Self

from pydantic import Field, model_validator

from story_engine.domain.errors import InvalidTransitionError
from story_engine.domain.models import (
    DomainModel,
    ReviewIssue,
    ReviewResult,
    StoryEvent,
)
from story_engine.rag.models import RetrievalEvidence

SceneDraftStatus = Literal[
    "draft",
    "reviewed",
    "needs_revision",
    "amendment_required",
    "saved",
]
AmendmentStatus = Literal["pending", "committed"]
SceneMutationStatus = Literal["saved", "amendment_required", "rejected"]


class WriterOutput(DomainModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=262_144)


class ManuscriptReviewOutput(DomainModel):
    review: ReviewResult
    new_facts: tuple[str, ...] = ()

    @model_validator(mode="after")
    def uses_manuscript_review_mode(self) -> Self:
        if self.review.mode != "manuscript_review":
            raise ValueError("manuscript review requires manuscript_review mode")
        return self

    @classmethod
    def passed(cls) -> "ManuscriptReviewOutput":
        return cls(
            review=ReviewResult(
                mode="manuscript_review",
                passed=True,
                summary="正文仅使用了已确认事实。",
            )
        )

    @classmethod
    def with_new_facts(
        cls,
        facts: tuple[str, ...],
    ) -> "ManuscriptReviewOutput":
        return cls(
            review=ReviewResult(
                mode="manuscript_review",
                passed=False,
                summary="正文包含尚未进入 Canon 的新事实。",
                issues=(
                    ReviewIssue(
                        code="new_fact_requires_amendment",
                        message="新增事实必须先创建并确认 Event Amendment。",
                        severity="blocking",
                    ),
                ),
            ),
            new_facts=facts,
        )


class Scene(DomainModel):
    id: str = Field(pattern=r"^scene-[0-9]{6}$")
    project_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    chapter_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=262_144)
    source_event_ids: tuple[str, ...] = Field(min_length=1)
    version: int = Field(ge=1)


class SceneDraft(DomainModel):
    id: str = Field(pattern=r"^scene-[0-9]{6}$")
    project_id: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    chapter_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=262_144)
    source_event_ids: tuple[str, ...] = Field(min_length=1)
    base_world_version: int = Field(ge=0)
    base_scene_version: int = Field(default=0, ge=0)
    base_workspace_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    revision: int = Field(default=0, ge=0)
    review: ManuscriptReviewOutput | None = None
    retrieval_evidence: tuple[RetrievalEvidence, ...] = ()
    amendment_id: str | None = None
    status: SceneDraftStatus = "draft"

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> Self:
        if (
            self.status in {"reviewed", "needs_revision", "amendment_required"}
            and self.review is None
        ):
            raise ValueError(f"{self.status} scene draft requires a review")
        if self.status == "amendment_required" and self.amendment_id is None:
            raise ValueError("amendment_required draft requires amendment id")
        if self.status == "reviewed" and (
            self.review is None
            or not self.review.review.passed
            or self.review.new_facts
        ):
            raise ValueError("reviewed draft requires a passing fact review")
        return self

    def with_content(self, *, title: str, body: str) -> "SceneDraft":
        if self.status == "saved":
            raise InvalidTransitionError("saved draft must be reloaded from its scene")
        return self.model_copy(
            update={
                "title": title,
                "body": body,
                "revision": self.revision + 1,
                "review": None,
                "amendment_id": None,
                "status": "draft",
            }
        )


class EventAmendmentCandidate(DomainModel):
    id: str = Field(pattern=r"^amendment-scene-[0-9]{6}-[0-9]{6}$")
    project_id: str = Field(min_length=1)
    scene_id: str = Field(pattern=r"^scene-[0-9]{6}$")
    source_event_ids: tuple[str, ...] = Field(min_length=1)
    proposed_facts: tuple[str, ...] = Field(min_length=1)
    fact_ids: tuple[str, ...] = Field(min_length=1)
    base_world_version: int = Field(ge=0)
    base_workspace_revision: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    draft_revision: int = Field(ge=1)
    status: AmendmentStatus = "pending"

    @model_validator(mode="after")
    def fact_ids_match_facts(self) -> Self:
        if len(self.fact_ids) != len(self.proposed_facts):
            raise ValueError("each proposed fact requires one fact id")
        return self

    def mark_committed(self) -> "EventAmendmentCandidate":
        if self.status != "pending":
            raise InvalidTransitionError("only a pending amendment can be committed")
        return self.model_copy(update={"status": "committed"})


class SceneGenerationRequest(DomainModel):
    event_ids: tuple[str, ...] = Field(min_length=1)
    chapter_id: str = Field(min_length=1, max_length=100)


class SceneUpdateRequest(DomainModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=262_144)
    expected_revision: int = Field(ge=0)
    expected_scene_version: int = Field(ge=0)


class SceneMutationResult(DomainModel):
    status: SceneMutationStatus
    draft: SceneDraft
    review: ManuscriptReviewOutput
    scene: Scene | None = None
    amendment: EventAmendmentCandidate | None = None


class AmendmentCommitResult(DomainModel):
    scene: Scene
    amendment: EventAmendmentCandidate
    event: StoryEvent


class ManuscriptExport(DomainModel):
    filename: str = Field(min_length=1)
    markdown: str
