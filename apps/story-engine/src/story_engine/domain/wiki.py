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
            self.expected_revision is not None
            or self.expected_content_hash is not None
        ):
            raise ValueError("create patch must not carry expected revision")
        return self


class WikiConsolidationOutput(RuntimeModel):
    patches: tuple[WikiPatch, ...] = Field(max_length=64)


class WikiPage(RuntimeModel):
    branch_id: Identifier
    path: str = Field(min_length=1, max_length=512)
    subject_id: Identifier | None = None
    updated_at_step: int = Field(ge=0)
    source_ids: tuple[Identifier, ...] = ()
    confidence: float = Field(default=1.0, ge=0, le=1)
    checkpoint_id: Identifier | None = None
    stale: bool = False
    content: str = Field(max_length=131_072)
    revision: int = Field(default=0, ge=0)
    content_hash: str = Field(default="", max_length=64)


class WikiPageSummary(RuntimeModel):
    path: str
    title: str
    subject_id: Identifier | None = None
    updated_at_step: int = Field(ge=0)
    source_ids: tuple[Identifier, ...] = ()
    confidence: float = Field(ge=0, le=1)


class WikiBranchView(RuntimeModel):
    branch_id: Identifier
    checkpoint_id: Identifier | None = None
    updated_at_step: int = Field(ge=0)
    stale: bool
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
