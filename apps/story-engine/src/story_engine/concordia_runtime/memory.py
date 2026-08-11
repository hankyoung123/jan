import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Sequence

import numpy as np
from concordia.associative_memory import (  # type: ignore[import-untyped]
    basic_associative_memory,
)

from story_engine.domain.memory import (
    MemoryHit,
    MemoryQuery,
    MemoryRecord,
    MemoryRecordType,
    MemoryScope,
)

_MEMORY_PREFIX = "[story-memory]"
_HASH_VECTOR_DIMENSIONS = 96
_ENGLISH_WORD = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*")
_CHINESE_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def lexical_tokens(text: str) -> frozenset[str]:
    """Tokenize English words and Chinese character n-grams without a model."""

    normalized = text.casefold()
    tokens = set(_ENGLISH_WORD.findall(normalized))
    for run in _CHINESE_RUN.findall(normalized):
        if len(run) == 1:
            tokens.add(run)
            continue
        for width in (2, 3):
            tokens.update(
                run[index : index + width]
                for index in range(len(run) - width + 1)
            )
    return frozenset(tokens)


def concordia_hash_embedder(text: str) -> np.ndarray:
    """Return Concordia's private, non-model hash vector for in-memory ranking."""

    vector = np.zeros(_HASH_VECTOR_DIMENSIONS, dtype=float)
    for token in lexical_tokens(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % _HASH_VECTOR_DIMENSIONS
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    norm = float(np.linalg.norm(vector))
    return vector if math.isclose(norm, 0.0) else vector / norm


class ConcordiaMemoryCodec:
    """Encode typed records as natural-language-friendly Concordia strings."""

    def encode(self, record: MemoryRecord) -> str:
        metadata = {
            "actor_ids": list(record.actor_ids),
            "branch_id": record.branch_id,
            "confidence": record.confidence,
            "content_locale": record.content_locale,
            "created_at": record.created_at.isoformat(),
            "importance": record.importance,
            "location_ids": list(record.location_ids),
            "owner_id": record.owner_id,
            "record_id": record.record_id,
            "record_type": record.record_type.value,
            "scope": record.scope.value,
            "session_id": record.session_id,
            "source_record_ids": list(record.source_record_ids),
            "step": record.step,
            "tags": list(record.tags),
            "text_encoding": "json-string-fragment",
            "visible_to": list(record.visible_to),
        }
        encoded = json.dumps(
            metadata,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        type_prefix = ""
        if record.record_type == MemoryRecordType.PUTATIVE_EVENT:
            actor_prefix = f" {record.actor_ids[0]}:" if record.actor_ids else ""
            type_prefix = f"[putative_event]{actor_prefix} "
        elif record.record_type == MemoryRecordType.WORLD_EVENT:
            type_prefix = "[event] "
        encoded_text = json.dumps(record.text, ensure_ascii=False)[1:-1]
        return f"{type_prefix}{_MEMORY_PREFIX}{encoded} {encoded_text}"

    def decode(self, value: str) -> MemoryRecord | None:
        marker = value.find(_MEMORY_PREFIX)
        if marker < 0:
            return None
        payload = value[marker + len(_MEMORY_PREFIX) :]
        try:
            metadata, end = json.JSONDecoder().raw_decode(payload)
            if not isinstance(metadata, dict):
                return None
            remainder = payload[end:]
            if not remainder.startswith(" "):
                return None
            text_encoding = metadata.pop("text_encoding", None)
            text = remainder[1:]
            if text_encoding == "json-string-fragment":
                text = json.loads(f'"{text}"')
            return MemoryRecord(
                **metadata,
                text=text,
                raw_text=value,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return None


def _eligible(record: MemoryRecord, query: MemoryQuery) -> bool:
    return (
        (not query.record_types or record.record_type in query.record_types)
        and record.importance >= query.min_importance
        and (query.before_step is None or record.step < query.before_step)
        and (
            query.content_locale is None
            or record.content_locale == query.content_locale
        )
    )


def _overlap(requested: tuple[str, ...], actual: tuple[str, ...]) -> float:
    requested_set = set(requested)
    if not requested_set:
        return 0.0
    return len(requested_set & set(actual)) / len(requested_set)


def rank_memory_records(
    records: Iterable[MemoryRecord],
    query: MemoryQuery,
) -> tuple[MemoryHit, ...]:
    """Rank typed memories using structure, lexical overlap, importance, and age."""

    candidates = tuple(record for record in records if _eligible(record, query))
    if not candidates:
        return ()
    query_tokens = lexical_tokens(query.query_text)
    reference_step = query.before_step or max(record.step for record in candidates) + 1
    ranked: list[MemoryHit] = []
    for record in candidates:
        record_tokens = lexical_tokens(record.text)
        shared_tokens = query_tokens & record_tokens
        lexical = (
            len(shared_tokens)
            / math.sqrt(max(1, len(query_tokens)) * max(1, len(record_tokens)))
            if shared_tokens
            else 0.0
        )
        structural_scores = tuple(
            _overlap(requested, actual)
            for requested, actual in (
                (query.actor_ids, record.actor_ids),
                (query.location_ids, record.location_ids),
                (query.tags, record.tags),
            )
            if requested
        )
        structural = (
            sum(structural_scores) / len(structural_scores)
            if structural_scores
            else 0.0
        )
        if lexical <= 0 and structural <= 0:
            continue
        distance = max(0, reference_step - record.step)
        recency = math.exp(-distance / 120.0)
        score = (
            0.55 * lexical
            + 0.20 * structural
            + 0.15 * record.importance
            + 0.10 * recency
        )
        ranked.append(
            MemoryHit(
                record=record,
                score=score,
                lexical_score=lexical,
                recency_score=recency,
                importance_score=record.importance,
            )
        )
    ranked.sort(
        key=lambda hit: (hit.score, hit.record.step, hit.record.record_id),
        reverse=True,
    )
    return tuple(ranked[: query.limit])


class ConcordiaMemoryBank:
    """Typed project port backed by Concordia's AssociativeMemoryBank."""

    def __init__(
        self,
        *,
        owner_id: str,
        scope: MemoryScope,
        codec: ConcordiaMemoryCodec | None = None,
        allow_duplicates: bool | None = None,
        embedder: Callable[[str], np.ndarray] = concordia_hash_embedder,
    ) -> None:
        self._owner_id = owner_id
        self._scope = scope
        self._codec = codec or ConcordiaMemoryCodec()
        self._embedder = embedder
        self._allow_duplicates = (
            scope == MemoryScope.GAME_MASTER
            if allow_duplicates is None
            else allow_duplicates
        )
        self._bank = basic_associative_memory.AssociativeMemoryBank(
            sentence_embedder=self._embed,
            allow_duplicates=self._allow_duplicates,
        )
        self._committed_count = 0

    def _embed(self, text: str) -> np.ndarray:
        vector = np.asarray(self._embedder(text), dtype=float)
        if vector.ndim != 1 or vector.size == 0 or not np.isfinite(vector).all():
            raise ValueError("memory embedder must return one finite vector")
        norm = float(np.linalg.norm(vector))
        normalized = vector if math.isclose(norm, 0.0) else vector / norm
        return normalized

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def scope(self) -> MemoryScope:
        return self._scope

    @property
    def raw_bank(self) -> basic_associative_memory.AssociativeMemoryBank:
        return self._bank

    @property
    def codec(self) -> ConcordiaMemoryCodec:
        return self._codec

    def _validate_owner(self, record: MemoryRecord) -> None:
        if record.owner_id != self._owner_id or record.scope != self._scope:
            raise ValueError("memory record owner or scope does not match bank")

    def add(self, record: MemoryRecord) -> None:
        self._validate_owner(record)
        self._bank.add(self._codec.encode(record))

    def extend(self, records: Iterable[MemoryRecord]) -> None:
        for record in records:
            self.add(record)

    def _decode_many(self, values: Sequence[str]) -> tuple[MemoryRecord, ...]:
        records = (self._codec.decode(value) for value in values)
        return tuple(record for record in records if record is not None)

    def retrieve(self, query: MemoryQuery) -> Sequence[MemoryHit]:
        return rank_memory_records(
            self._decode_many(self._bank.get_all_memories_as_text()),
            query,
        )

    def retrieve_recent(
        self,
        *,
        limit: int,
        record_types: tuple[MemoryRecordType, ...] = (),
    ) -> Sequence[MemoryRecord]:
        if limit <= 0:
            raise ValueError("memory limit must be positive")
        values = self._bank.get_all_memories_as_text()
        records = self._decode_many(values)
        if record_types:
            records = tuple(
                record for record in records if record.record_type in record_types
            )
        return records[-limit:]

    def scan(
        self,
        predicate: Callable[[MemoryRecord], bool],
    ) -> Sequence[MemoryRecord]:
        return tuple(
            record
            for record in self._decode_many(self._bank.get_all_memories_as_text())
            if predicate(record)
        )

    def records(self) -> tuple[MemoryRecord, ...]:
        return self._decode_many(self._bank.get_all_memories_as_text())

    def pending_records(self) -> tuple[MemoryRecord, ...]:
        return self.records()[self._committed_count :]

    def mark_committed(self) -> None:
        self._committed_count = len(self._bank)

    def replay(self, records: Iterable[MemoryRecord]) -> None:
        self.extend(records)
        self.mark_committed()

    def replace(self, records: Iterable[MemoryRecord]) -> None:
        empty = basic_associative_memory.AssociativeMemoryBank(
            sentence_embedder=self._embed,
            allow_duplicates=self._allow_duplicates,
        )
        self._bank.set_state(empty.get_state())
        self.extend(records)
        self.mark_committed()

    def flush(self) -> None:
        self._bank.get_data_frame()
