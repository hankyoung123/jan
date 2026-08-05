from typing import Literal, Self

from pydantic import Field, model_validator

from story_engine.domain.base import Identifier, LocaleCode
from story_engine.domain.models import DomainModel, ReviewIssue, ReviewResult

SceneDraftStatus = Literal["draft", "reviewed", "needs_revision", "saved"]
SceneMutationStatus = Literal["saved", "rejected"]


class ProjectCreativeContext(DomainModel):
    project_id: Identifier
    title: str = Field(min_length=1, max_length=512)
    genre: str = Field(min_length=1, max_length=512)
    theme: str = Field(min_length=1, max_length=2_048)
    tone: str = Field(min_length=1, max_length=2_048)
    content_locale: LocaleCode


class WriterOutput(DomainModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=262_144)


class ManuscriptReviewOutput(DomainModel):
    review: ReviewResult
    unsupported_facts: tuple[str, ...] = ()

    @model_validator(mode="after")
    def uses_manuscript_review_mode(self) -> Self:
        if self.review.mode != "manuscript_review":
            raise ValueError("manuscript review requires manuscript_review mode")
        if self.review.passed and self.unsupported_facts:
            raise ValueError("passing review cannot contain unsupported facts")
        return self

    @classmethod
    def passed(cls) -> "ManuscriptReviewOutput":
        return cls(
            review=ReviewResult(
                mode="manuscript_review",
                passed=True,
                summary="正文中的具体事实均可追溯到模拟来源。",
            )
        )

    @classmethod
    def with_unsupported_facts(
        cls,
        facts: tuple[str, ...],
    ) -> "ManuscriptReviewOutput":
        return cls(
            review=ReviewResult(
                mode="manuscript_review",
                passed=False,
                summary="正文包含模拟来源不支持的具体事实。",
                issues=tuple(
                    ReviewIssue(
                        code="unsupported_fact",
                        message=fact,
                        severity="blocking",
                    )
                    for fact in facts
                ),
            ),
            unsupported_facts=facts,
        )


class Scene(DomainModel):
    id: str = Field(pattern=r"^scene-[0-9]{6}$")
    project_id: Identifier
    branch_id: Identifier
    sequence: int = Field(ge=1)
    chapter_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=262_144)
    source_checkpoint_id: Identifier
    source_from_step: int = Field(ge=0)
    source_to_step: int = Field(ge=0)
    source_event_ids: tuple[Identifier, ...] = Field(min_length=1)
    source_memory_ids: tuple[Identifier, ...]
    source_wiki_branch_id: Identifier
    source_wiki_version_id: str = Field(min_length=1)
    viewpoint_actor_id: Identifier | None = None
    version: int = Field(ge=1)


class SceneDraft(DomainModel):
    id: str = Field(pattern=r"^scene-[0-9]{6}$")
    project_id: Identifier
    branch_id: Identifier
    sequence: int = Field(ge=1)
    chapter_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=262_144)
    source_checkpoint_id: Identifier
    source_from_step: int = Field(ge=0)
    source_to_step: int = Field(ge=0)
    source_event_ids: tuple[Identifier, ...] = Field(min_length=1)
    source_memory_ids: tuple[Identifier, ...]
    source_wiki_branch_id: Identifier
    source_wiki_version_id: str = Field(min_length=1)
    viewpoint_actor_id: Identifier | None = None
    base_scene_version: int = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)
    review: ManuscriptReviewOutput | None = None
    status: SceneDraftStatus = "draft"

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> Self:
        if self.source_to_step < self.source_from_step:
            raise ValueError("scene source step range is reversed")
        if self.status in {"reviewed", "needs_revision"} and self.review is None:
            raise ValueError(f"{self.status} scene draft requires a review")
        if self.status == "reviewed" and (
            self.review is None
            or not self.review.review.passed
            or self.review.unsupported_facts
        ):
            raise ValueError("reviewed draft requires a grounded review")
        return self

    def with_content(self, *, title: str, body: str) -> "SceneDraft":
        return self.model_copy(
            update={
                "title": title,
                "body": body,
                "revision": self.revision + 1,
                "review": None,
                "status": "draft",
            }
        )


class SceneGenerationRequest(DomainModel):
    checkpoint_id: Identifier
    from_step: int = Field(ge=0)
    to_step: int = Field(ge=0)
    chapter_id: str = Field(min_length=1, max_length=100)
    viewpoint_actor_id: Identifier | None = None

    @model_validator(mode="after")
    def step_range_is_ordered(self) -> Self:
        if self.to_step < self.from_step:
            raise ValueError("scene source step range is reversed")
        return self


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


class ManuscriptExport(DomainModel):
    project_id: Identifier
    branch_id: Identifier
    checkpoint_id: Identifier | None
    filename: str = Field(min_length=1)
    markdown: str
