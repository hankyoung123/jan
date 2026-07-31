import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, cast

import frontmatter
from pydantic import ValidationError

from story_engine.rag.models import (
    RagChunk,
    RagHit,
    RagIndex,
    RagSearchRequest,
    RagSearchResult,
    RagSourceType,
    RetrievalScope,
)
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import ProjectDocument, load_document

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_TOKEN_RUN = re.compile(r"[a-z0-9_:.\-/]+|[\u3400-\u4dbf\u4e00-\u9fff]+", re.I)
_MAX_CHUNK_CHARS = 1_200


def _slug(value: str) -> str:
    normalized = re.sub(r"[^\w]+", "-", value.casefold(), flags=re.UNICODE).strip("-")
    return normalized or "section"


def _tokens(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for run in _TOKEN_RUN.findall(value.casefold()):
        if any("\u3400" <= character <= "\u9fff" for character in run):
            tokens.extend(run)
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
        else:
            tokens.append(run)
    return tuple(tokens)


def _split_content(value: str) -> tuple[str, ...]:
    stripped = value.strip()
    if not stripped:
        return ()
    paragraphs = re.split(r"\n\s*\n", stripped)
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= _MAX_CHUNK_CHARS:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(paragraph) > _MAX_CHUNK_CHARS:
            chunks.append(paragraph[:_MAX_CHUNK_CHARS])
            paragraph = paragraph[_MAX_CHUNK_CHARS:]
        current = paragraph
    if current:
        chunks.append(current)
    return tuple(chunks)


class RagService:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index_path = root / ".story-engine/index/rag-v1.json"

    def _document_paths(self) -> tuple[Path, ...]:
        candidates = [self.root / "project.md", self.root / "world.md"]
        for directory in ("characters", "events", "scenes", "sources"):
            candidates.extend(sorted((self.root / directory).rglob("*.md")))
        return tuple(
            path
            for path in sorted(set(candidates), key=lambda item: item.as_posix())
            if path.is_file() and ".story-engine" not in path.parts
        )

    def _fingerprint(self, paths: tuple[Path, ...]) -> str:
        digest = hashlib.sha256()
        for path in paths:
            digest.update(path.relative_to(self.root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()

    def _source_type(self, path: Path) -> RagSourceType:
        relative = path.relative_to(self.root)
        if relative == Path("project.md"):
            return "project"
        if relative == Path("world.md"):
            return "world"
        directory = relative.parts[0]
        mapping: dict[str, RagSourceType] = {
            "characters": "character",
            "events": "event",
            "scenes": "scene",
            "sources": "source",
        }
        return mapping[directory]

    def _chunks_for(self, path: Path) -> tuple[RagChunk, ...]:
        relative = path.relative_to(self.root).as_posix()
        post = frontmatter.loads(path.read_text(encoding="utf-8"))
        metadata = cast(dict[str, Any], dict(post.metadata))
        body = str(post.content)
        source_type = self._source_type(path)
        metadata_id = metadata.get("id")
        source_id = (
            (
                "source:"
                f"{path.relative_to(self.root / 'sources').with_suffix('').as_posix()}"
            )
            if source_type == "source"
            else "world"
            if source_type == "world"
            else metadata_id
            if isinstance(metadata_id, str) and metadata_id
            else path.stem
        )
        character_id = source_id if source_type == "character" else None
        participant_value = metadata.get("participants", ())
        participant_ids = (
            tuple(str(value) for value in participant_value)
            if isinstance(participant_value, (list, tuple))
            else ()
        )
        chunks: list[RagChunk] = []
        if metadata:
            chunks.append(
                RagChunk(
                    chunk_id=f"{source_id}#frontmatter",
                    source_type=source_type,
                    source_id=source_id,
                    source_path=relative,
                    heading="Front Matter",
                    section_key="frontmatter",
                    content=json.dumps(
                        metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ),
                    character_id=character_id,
                    participant_ids=participant_ids,
                    front_matter=True,
                )
            )

        matches = list(_HEADING.finditer(body))
        sections: list[tuple[str, str]] = []
        if matches and body[: matches[0].start()].strip():
            sections.append(("Preamble", body[: matches[0].start()]))
        if matches:
            for index, match in enumerate(matches):
                end = (
                    matches[index + 1].start()
                    if index + 1 < len(matches)
                    else len(body)
                )
                sections.append((match.group(2).strip(), body[match.start() : end]))
        elif body.strip():
            sections.append((path.stem, body))

        section_counts: Counter[str] = Counter()
        for heading, section in sections:
            section_key = _slug(heading)
            section_counts[section_key] += 1
            occurrence = section_counts[section_key]
            for part, content in enumerate(_split_content(section), start=1):
                chunks.append(
                    RagChunk(
                        chunk_id=(
                            f"{source_id}#{section_key}-{occurrence}-{part}"
                        ),
                        source_type=source_type,
                        source_id=source_id,
                        source_path=relative,
                        heading=heading,
                        section_key=section_key,
                        content=content,
                        character_id=character_id,
                        participant_ids=participant_ids,
                    )
                )
        return tuple(chunks)

    def rebuild_index(self) -> RagIndex:
        paths = self._document_paths()
        project, _body = load_document(self.root / "project.md", ProjectDocument)
        chunks = tuple(chunk for path in paths for chunk in self._chunks_for(path))
        index = RagIndex(
            project_id=project.id,
            fingerprint=self._fingerprint(paths),
            document_count=len(paths),
            chunk_count=len(chunks),
            chunks=chunks,
        )
        atomic_write_text(
            self.index_path,
            f"{index.model_dump_json(indent=2, by_alias=True)}\n",
        )
        return index

    def ensure_index(self) -> RagIndex:
        paths = self._document_paths()
        fingerprint = self._fingerprint(paths)
        try:
            index = RagIndex.model_validate_json(
                self.index_path.read_text(encoding="utf-8")
            )
        except (FileNotFoundError, OSError, ValidationError, ValueError):
            return self.rebuild_index()
        if index.fingerprint != fingerprint:
            return self.rebuild_index()
        return index

    @staticmethod
    def _authorized(chunk: RagChunk, scope: RetrievalScope) -> bool:
        if scope.kind == "editorial":
            return True
        if scope.kind == "writer":
            if chunk.source_type in {"project", "scene"}:
                return True
            if chunk.source_type == "source":
                return chunk.source_id == "source:style"
            if chunk.source_type == "world":
                return not chunk.front_matter and chunk.section_key in {
                    "world",
                    "current-state",
                    "rules",
                    "active-pressures",
                    "public-facts",
                }
            return (
                chunk.source_type == "event"
                and not chunk.front_matter
                and chunk.section_key != "hidden-results"
            )

        character_id = scope.character_id
        if chunk.source_type == "character":
            return chunk.character_id == character_id
        if chunk.front_matter:
            return False
        if chunk.source_type == "world":
            return chunk.section_key in {
                "world",
                "current-state",
                "rules",
                "public-facts",
            }
        if chunk.source_type == "event":
            return (
                character_id in chunk.participant_ids
                and chunk.section_key != "hidden-results"
            )
        return False

    @staticmethod
    def _hit(
        chunk: RagChunk,
        *,
        scope: RetrievalScope,
        score: float,
        mode: str,
    ) -> RagHit:
        return RagHit(
            chunk_id=chunk.chunk_id,
            source_type=chunk.source_type,
            source_id=chunk.source_id,
            source_path=chunk.source_path,
            heading=chunk.heading,
            permission_scope=scope.label,
            score=score,
            retrieval_mode=cast(Any, mode),
            content=chunk.content,
        )

    def search(self, request: RagSearchRequest) -> RagSearchResult:
        index = self.ensure_index()
        authorized = tuple(
            chunk for chunk in index.chunks if self._authorized(chunk, request.scope)
        )
        if request.exact_id:
            exact_chunks = tuple(
                chunk
                for chunk in authorized
                if chunk.chunk_id == request.exact_id
                or chunk.source_id == request.exact_id
            )
            hits = tuple(
                self._hit(
                    chunk,
                    scope=request.scope,
                    score=1.0,
                    mode="exact",
                )
                for chunk in exact_chunks[: request.limit]
            )
        else:
            hits = self._bm25(
                authorized,
                query=request.query,
                scope=request.scope,
                limit=request.limit,
            )
        return RagSearchResult(
            query=request.query,
            exact_id=request.exact_id,
            permission_scope=request.scope.label,
            index_fingerprint=index.fingerprint,
            hits=hits,
        )

    def _bm25(
        self,
        chunks: tuple[RagChunk, ...],
        *,
        query: str,
        scope: RetrievalScope,
        limit: int,
    ) -> tuple[RagHit, ...]:
        query_terms = _tokens(query)
        if not query_terms or not chunks:
            return ()
        documents = tuple(_tokens(chunk.content) for chunk in chunks)
        average_length = sum(len(document) for document in documents) / len(documents)
        document_frequency = Counter(
            term for document in documents for term in set(document)
        )
        query_counts = Counter(query_terms)
        ranked: list[tuple[float, str, RagChunk]] = []
        k1 = 1.5
        b = 0.75
        for chunk, document in zip(chunks, documents, strict=True):
            frequencies = Counter(document)
            score = 0.0
            for term, query_weight in query_counts.items():
                frequency = frequencies[term]
                if frequency == 0:
                    continue
                frequency_in_docs = document_frequency[term]
                inverse_frequency = math.log(
                    1
                    + (len(documents) - frequency_in_docs + 0.5)
                    / (frequency_in_docs + 0.5)
                )
                denominator = frequency + k1 * (
                    1 - b + b * len(document) / max(average_length, 1)
                )
                score += (
                    inverse_frequency
                    * frequency
                    * (k1 + 1)
                    / denominator
                    * query_weight
                )
            title_tokens = set(_tokens(f"{chunk.source_id} {chunk.heading}"))
            score += 0.35 * len(title_tokens.intersection(query_counts))
            if score > 0:
                ranked.append((score, chunk.chunk_id, chunk))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return tuple(
            self._hit(chunk, scope=scope, score=score, mode="bm25")
            for score, _chunk_id, chunk in ranked[:limit]
        )
