from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from story_engine.domain.base import Identifier, LocaleCode
from story_engine.domain.memory import MemoryRecord
from story_engine.domain.models import DomainModel, ReviewIssue, ReviewResult
from story_engine.domain.projection import ResolvedEvent, SimulationBoundary
from story_engine.domain.wiki import WikiContextManifestEntry

SceneDraftStatus = Literal["draft", "reviewed", "needs_revision", "saved"]
SceneMutationStatus = Literal["saved", "rejected"]
SourceSelectionDecision = Literal["ready", "not_ready"]
SourceCandidateStatus = Literal["available", "covered"]


class ProjectCreativeContext(DomainModel):
    project_id: Identifier
    title: str = Field(min_length=1, max_length=512)
    genre: str = Field(min_length=1, max_length=512)
    theme: str = Field(min_length=1, max_length=2_048)
    tone: str = Field(min_length=1, max_length=2_048)
    content_locale: LocaleCode


class ManualSourceSelection(DomainModel):
    mode: Literal["manual"] = "manual"
    source_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=32)


class SceneSourceSelection(DomainModel):
    mode: Literal["scene"] = "scene"
    source_id: Identifier


class WriterSourceSelection(DomainModel):
    mode: Literal["writer"] = "writer"


SourceSelection = Annotated[
    ManualSourceSelection | SceneSourceSelection | WriterSourceSelection,
    Field(discriminator="mode"),
]


class ManuscriptGenerationRequest(DomainModel):
    source: SourceSelection
    chapter_id: str = Field(min_length=1, max_length=100)
    viewpoint_actor_id: Identifier | None = None
    target_words: int | None = Field(default=None, ge=1, le=50_000)
    instruction: str | None = Field(default=None, max_length=16_384)


class ManuscriptSourceCandidate(DomainModel):
    """A bounded, branch-local range that can be frozen as manuscript input."""

    source_id: Identifier
    branch_id: Identifier
    checkpoint_id: Identifier
    from_step: int = Field(ge=0)
    to_step: int = Field(ge=0)
    boundary: SimulationBoundary
    title_hint: str = Field(min_length=1, max_length=512)
    event_summary_text: str = Field(min_length=1, max_length=131_072)
    event_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=128)
    estimated_chars: int = Field(ge=1, le=131_072)
    available_viewpoint_ids: tuple[Identifier, ...] = ()
    wiki_version_id: str = Field(min_length=1)
    status: SourceCandidateStatus = "available"

    @model_validator(mode="after")
    def range_is_valid(self) -> Self:
        if self.to_step < self.from_step:
            raise ValueError("manuscript source step range is reversed")
        if len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("manuscript source event ids must be unique")
        return self


class SourceSelectionResult(DomainModel):
    decision: SourceSelectionDecision
    source_ids: tuple[Identifier, ...] = Field(default=(), max_length=32)
    reason: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def selection_matches_decision(self) -> Self:
        if self.decision == "ready" and not self.source_ids:
            raise ValueError("ready source selection requires source_ids")
        if self.decision == "not_ready" and self.source_ids:
            raise ValueError("not_ready source selection cannot contain source_ids")
        return self


class ManuscriptSourceManifest(DomainModel):
    """Immutable lineage chosen before the writer is called."""

    project_id: Identifier
    branch_id: Identifier
    checkpoint_id: Identifier
    source_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=32)
    from_step: int = Field(ge=0)
    to_step: int = Field(ge=0)
    event_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=128)
    memory_ids: tuple[Identifier, ...] = Field(default=(), max_length=128)
    wiki_version_id: str = Field(min_length=1)
    viewpoint_actor_id: Identifier | None = None

    @model_validator(mode="after")
    def manifest_is_consistent(self) -> Self:
        if self.to_step < self.from_step:
            raise ValueError("manuscript source step range is reversed")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("manuscript source ids must be unique")
        if len(self.event_ids) != len(set(self.event_ids)):
            raise ValueError("manuscript event ids must be unique")
        if len(self.memory_ids) != len(set(self.memory_ids)):
            raise ValueError("manuscript memory ids must be unique")
        return self


class ManuscriptFactContext(DomainModel):
    """The only material the writer or editor may treat as story fact."""

    events: tuple[ResolvedEvent, ...] = Field(min_length=1, max_length=128)
    memories: tuple[MemoryRecord, ...] = Field(default=(), max_length=128)
    wiki_context: str = Field(default="", max_length=32_768)
    wiki_manifest: tuple[WikiContextManifestEntry, ...] = Field(
        default=(), max_length=128
    )
    project: ProjectCreativeContext

    @model_validator(mode="after")
    def facts_are_unique(self) -> Self:
        event_ids = tuple(event.event_id for event in self.events)
        memory_ids = tuple(memory.record_id for memory in self.memories)
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("manuscript fact events must be unique")
        if len(memory_ids) != len(set(memory_ids)):
            raise ValueError("manuscript fact memories must be unique")
        return self


class ManuscriptContinuityContext(DomainModel):
    previous_scene_id: str | None = None
    previous_scene_title: str | None = Field(default=None, max_length=200)
    previous_scene_excerpt: str = Field(default="", max_length=2_000)


class ManuscriptWritingIntent(DomainModel):
    chapter_id: str = Field(min_length=1, max_length=100)
    target_words: int | None = Field(default=None, ge=1, le=50_000)
    instruction: str | None = Field(default=None, max_length=16_384)


class ManuscriptContext(DomainModel):
    source: ManuscriptSourceManifest
    facts: ManuscriptFactContext
    continuity: ManuscriptContinuityContext
    intent: ManuscriptWritingIntent

    @model_validator(mode="after")
    def context_matches_source(self) -> Self:
        def is_ordered_subset(values: tuple[str, ...], source: tuple[str, ...]) -> bool:
            source_index = 0
            for value in values:
                try:
                    source_index = source.index(value, source_index) + 1
                except ValueError:
                    return False
            return True

        fact_event_ids = tuple(event.event_id for event in self.facts.events)
        source_event_ids = self.source.event_ids
        if not is_ordered_subset(fact_event_ids, source_event_ids):
            raise ValueError("manuscript facts do not match source event ids")
        fact_memory_ids = tuple(memory.record_id for memory in self.facts.memories)
        source_memory_ids = self.source.memory_ids
        if not is_ordered_subset(fact_memory_ids, source_memory_ids):
            raise ValueError("manuscript facts do not match source memory ids")
        if self.facts.project.project_id != self.source.project_id:
            raise ValueError("manuscript facts do not match source project")
        return self


class ManuscriptContextManifest(DomainModel):
    """Small durable audit record; it never stores prompt or fact bodies."""

    wiki_page_paths: tuple[str, ...] = Field(default=(), max_length=128)
    memory_ids: tuple[Identifier, ...] = Field(default=(), max_length=128)
    previous_scene_id: str | None = None
    selected_source_ids: tuple[Identifier, ...] = Field(max_length=32)
    selection_reason: str = Field(min_length=1, max_length=2_000)
    target_words: int | None = Field(default=None, ge=1, le=50_000)
    instruction: str | None = Field(default=None, max_length=16_384)


class WriterOutput(DomainModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=262_144)


class EditorReviewProposal(DomainModel):
    summary: str = Field(min_length=1, max_length=8_000)
    issues: tuple[str, ...] = Field(default=(), max_length=128)


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
    source: ManuscriptSourceManifest
    context_manifest: ManuscriptContextManifest
    version: int = Field(ge=1)


class SceneDraft(DomainModel):
    id: str = Field(pattern=r"^scene-[0-9]{6}$")
    project_id: Identifier
    branch_id: Identifier
    sequence: int = Field(ge=1)
    chapter_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=262_144)
    source: ManuscriptSourceManifest
    context_manifest: ManuscriptContextManifest
    base_scene_version: int = Field(default=0, ge=0)
    revision: int = Field(default=0, ge=0)
    review: ManuscriptReviewOutput | None = None
    status: SceneDraftStatus = "draft"

    @model_validator(mode="after")
    def lifecycle_is_consistent(self) -> Self:
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
