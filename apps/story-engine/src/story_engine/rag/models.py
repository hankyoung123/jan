from typing import Literal, Self

from pydantic import Field, model_validator

from story_engine.domain.models import DomainModel

RagSourceType = Literal["project", "world", "character", "fact", "source"]
RagScopeKind = Literal["editorial", "writer", "character"]
RagRetrievalMode = Literal["exact", "bm25"]
RagTask = Literal["writer", "editor"]


class RetrievalScope(DomainModel):
    kind: RagScopeKind
    character_id: str | None = None

    @model_validator(mode="after")
    def character_scope_has_exact_owner(self) -> Self:
        if self.kind == "character" and not self.character_id:
            raise ValueError("character scope requires character_id")
        if self.kind != "character" and self.character_id is not None:
            raise ValueError("only character scope accepts character_id")
        return self

    @property
    def label(self) -> str:
        if self.kind == "character":
            return f"character:{self.character_id}"
        return self.kind


class RagChunk(DomainModel):
    chunk_id: str = Field(min_length=1)
    source_type: RagSourceType
    source_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    heading: str = Field(min_length=1)
    section_key: str = Field(min_length=1)
    content: str = Field(min_length=1)
    character_id: str | None = None
    participant_ids: tuple[str, ...] = ()
    fact_visibility: Literal["public", "private", "secret"] | None = None
    fact_known_by: tuple[str, ...] = ()
    front_matter: bool = False


class RagIndex(DomainModel):
    schema_name: Literal["rag-index/v2"] = Field(
        default="rag-index/v2",
        serialization_alias="schema",
        validation_alias="schema",
    )
    project_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=64, max_length=64)
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    document_signatures: dict[str, str]
    document_hashes: dict[str, str]
    chunks: tuple[RagChunk, ...]

    @model_validator(mode="after")
    def count_matches_chunks(self) -> Self:
        if self.chunk_count != len(self.chunks):
            raise ValueError("chunk_count must match chunks")
        if self.document_count != len(self.document_signatures):
            raise ValueError("document_count must match signatures")
        if set(self.document_signatures) != set(self.document_hashes):
            raise ValueError("RAG signatures and hashes must cover the same documents")
        if any(len(value) != 64 for value in self.document_hashes.values()):
            raise ValueError("RAG document hashes must be SHA-256 values")
        chunk_ids = [chunk.chunk_id for chunk in self.chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("RAG chunk IDs must be unique")
        return self


class RagIndexSummary(DomainModel):
    project_id: str = Field(min_length=1)
    fingerprint: str = Field(min_length=64, max_length=64)
    document_count: int = Field(ge=0)
    chunk_count: int = Field(ge=0)

    @classmethod
    def from_index(cls, index: RagIndex) -> "RagIndexSummary":
        return cls(
            project_id=index.project_id,
            fingerprint=index.fingerprint,
            document_count=index.document_count,
            chunk_count=index.chunk_count,
        )


class RagSearchRequest(DomainModel):
    query: str = Field(default="", max_length=4_096)
    exact_id: str | None = Field(default=None, max_length=300)
    scope: RetrievalScope
    limit: int = Field(default=8, ge=1, le=50)

    @model_validator(mode="after")
    def has_query_or_exact_identifier(self) -> Self:
        if not self.query and not self.exact_id:
            raise ValueError("RAG search requires query or exact_id")
        return self


class RagHit(DomainModel):
    chunk_id: str = Field(min_length=1)
    source_type: RagSourceType
    source_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    heading: str = Field(min_length=1)
    permission_scope: str = Field(min_length=1)
    score: float = Field(gt=0)
    retrieval_mode: RagRetrievalMode
    content: str = Field(min_length=1)


class RetrievalEvidence(RagHit):
    task: RagTask


class RagSearchResult(DomainModel):
    query: str
    exact_id: str | None = None
    permission_scope: str = Field(min_length=1)
    index_fingerprint: str = Field(min_length=64, max_length=64)
    hits: tuple[RagHit, ...]
