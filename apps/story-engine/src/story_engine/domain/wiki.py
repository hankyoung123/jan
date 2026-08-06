from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from story_engine.domain.base import Identifier, RuntimeModel


class WikiPatchOperation(StrEnum):
    CREATE = "create"
    REPLACE_SECTION = "replace_section"
    APPEND_HISTORY = "append_history"
    ARCHIVE_SECTION = "archive_section"


class WikiSourceKind(StrEnum):
    PROJECT = "project"
    PROFILE = "profile"
    ACTION = "action"
    EVENT = "event"
    GM_MEMORY = "gm_memory"
    OBSERVATION = "observation"
    DIRECTOR_INSTRUCTION = "director_instruction"


def validate_wiki_visibility(value: str) -> str:
    if value in {"public", "gm_only"}:
        return value
    if value.startswith("private:") and len(value.removeprefix("private:")) > 0:
        return value
    raise ValueError("Wiki visibility must be public, gm_only, or private:<actor_id>")


class WikiSource(RuntimeModel):
    source_id: Identifier
    kind: WikiSourceKind
    branch_id: Identifier
    step: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=131_072)
    subject_id: Identifier | None = None


class WikiPatch(RuntimeModel):
    path: str = Field(min_length=1, max_length=512)
    section: str | None = Field(default=None, max_length=256)
    operation: WikiPatchOperation
    content: str = Field(min_length=1, max_length=65_536)
    source_ids: tuple[Identifier, ...] = Field(min_length=1, max_length=256)
    confidence: float = Field(default=1.0, ge=0, le=1)
    expected_revision: int | None = Field(default=None, ge=0)
    expected_content_hash: str | None = Field(default=None, max_length=64)
    visibility: str | None = Field(
        default=None,
        pattern=r"^(public|gm_only|private:.+)$",
    )
    proposal_page_id: Identifier | None = None
    proposal_source_refs: tuple[int, ...] = ()

    @model_validator(mode="after")
    def section_matches_operation(self) -> Self:
        needs_section = self.operation in {
            WikiPatchOperation.REPLACE_SECTION,
            WikiPatchOperation.ARCHIVE_SECTION,
        }
        if needs_section != (self.section is not None):
            raise ValueError("wiki patch section does not match its operation")
        if len(self.source_ids) != len(set(self.source_ids)):
            raise ValueError("wiki patch source ids must be unique")
        if self.operation == WikiPatchOperation.CREATE and (
            self.expected_revision is not None or self.expected_content_hash is not None
        ):
            raise ValueError("create patch must not carry expected revision")
        if self.visibility is not None:
            validate_wiki_visibility(self.visibility)
        if (self.proposal_page_id is None) != (not self.proposal_source_refs):
            raise ValueError("wiki proposal trace requires page id and source refs")
        if len(self.proposal_source_refs) != len(set(self.proposal_source_refs)):
            raise ValueError("wiki proposal source refs must be unique")
        return self


class WikiUpdate(RuntimeModel):
    page_id: Identifier
    content: str = Field(min_length=1, max_length=65_536)
    source_refs: tuple[int, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def source_refs_are_unique(self) -> Self:
        if len(self.source_refs) != len(set(self.source_refs)):
            raise ValueError("wiki update source refs must be unique")
        if any(item < 0 for item in self.source_refs):
            raise ValueError("wiki update source refs must be non-negative")
        return self


class WikiUpdateProposal(RuntimeModel):
    updates: tuple[WikiUpdate, ...] = Field(max_length=64)


class WikiPage(RuntimeModel):
    branch_id: Identifier
    path: str = Field(min_length=1, max_length=512)
    subject_id: Identifier | None = None
    updated_at_step: int = Field(ge=0)
    source_ids: tuple[Identifier, ...] = ()
    confidence: float = Field(default=1.0, ge=0, le=1)
    checkpoint_id: Identifier | None = None
    stale: bool = False
    degraded: bool = False
    degradation_reason: str | None = Field(default=None, max_length=8_000)
    content: str = Field(max_length=131_072)
    visibility: str = Field(default="public", pattern=r"^(public|gm_only|private:.+)$")
    revision: int = Field(default=0, ge=0)
    content_hash: str = Field(default="", max_length=64)

    @model_validator(mode="after")
    def visibility_is_valid(self) -> Self:
        validate_wiki_visibility(self.visibility)
        if self.degraded and not self.stale:
            raise ValueError("degraded Wiki page must also be stale")
        if (self.degradation_reason is not None) != self.degraded:
            raise ValueError("Wiki degradation reason must match degraded state")
        return self


class WikiPageSummary(RuntimeModel):
    path: str
    title: str
    subject_id: Identifier | None = None
    updated_at_step: int = Field(ge=0)
    source_ids: tuple[Identifier, ...] = ()
    confidence: float = Field(ge=0, le=1)
    visibility: str = Field(default="public", pattern=r"^(public|gm_only|private:.+)$")


class WikiBranchView(RuntimeModel):
    branch_id: Identifier
    checkpoint_id: Identifier | None = None
    updated_at_step: int = Field(ge=0)
    stale: bool
    degraded: bool = False
    degradation_reason: str | None = Field(default=None, max_length=8_000)
    pages: tuple[WikiPageSummary, ...]


class WikiContextManifestEntry(RuntimeModel):
    path: str = Field(min_length=1, max_length=1_024)
    reason: Identifier
    permission: str = Field(min_length=1, max_length=256)
    source_ids: tuple[Identifier, ...] = ()
    estimated_tokens: int = Field(ge=0)


class WikiContextBundle(RuntimeModel):
    content: str
    manifest: tuple[WikiContextManifestEntry, ...] = ()


class WikiLintSeverity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


class WikiLintIssue(RuntimeModel):
    code: Identifier
    message: str = Field(min_length=1, max_length=2_048)
    path: str | None = Field(default=None, max_length=512)
    severity: WikiLintSeverity


class WikiLintResult(RuntimeModel):
    branch_id: Identifier
    passed: bool
    issues: tuple[WikiLintIssue, ...] = ()


class DirectorInstruction(RuntimeModel):
    instruction_id: Identifier
    text: str = Field(min_length=1, max_length=65_536)
    created_at: datetime
    applies_from_checkpoint_id: Identifier
