import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any, cast

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
    MemorySnapshot,
)

_MEMORY_PREFIX = "[story-memory]"
_MEMORY_PATTERN = re.compile(r"\[story-memory\](\{.*?\})\s(.*)$")
_HASH_VECTOR_DIMENSIONS = 96


def concordia_hash_embedder(text: str) -> np.ndarray:
    """Return Concordia's private, non-model hash vector for in-memory ranking."""

    vector = np.zeros(_HASH_VECTOR_DIMENSIONS, dtype=float)
    normalized = " ".join(text.casefold().split())
    tokens = normalized.split() or list(normalized)
    for token in tokens:
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
        return f"{type_prefix}{_MEMORY_PREFIX}{encoded} {record.text}"

    def decode(self, value: str) -> MemoryRecord | None:
        match = _MEMORY_PATTERN.search(value)
        if match is None:
            return None
        try:
            metadata = json.loads(match.group(1))
            if not isinstance(metadata, dict):
                return None
            return MemoryRecord(
                **metadata,
                text=match.group(2),
                raw_text=value,
            )
        except (TypeError, ValueError):
            return None


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
        self._vector_cache: dict[str, np.ndarray] = {}
        self._bank = basic_associative_memory.AssociativeMemoryBank(
            sentence_embedder=self._embed,
            allow_duplicates=(
                scope == MemoryScope.GAME_MASTER
                if allow_duplicates is None
                else allow_duplicates
            ),
        )

    def _embed(self, text: str) -> np.ndarray:
        cached = self._vector_cache.get(text)
        if cached is not None:
            return cached
        vector = np.asarray(self._embedder(text), dtype=float)
        if vector.ndim != 1 or vector.size == 0 or not np.isfinite(vector).all():
            raise ValueError("memory embedder must return one finite vector")
        norm = float(np.linalg.norm(vector))
        normalized = vector if math.isclose(norm, 0.0) else vector / norm
        self._vector_cache[text] = normalized
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

    @staticmethod
    def _matches(record: MemoryRecord, query: MemoryQuery) -> bool:
        return (
            (not query.record_types or record.record_type in query.record_types)
            and (
                not query.actor_ids
                or bool(set(record.actor_ids) & set(query.actor_ids))
            )
            and (
                not query.location_ids
                or bool(set(record.location_ids) & set(query.location_ids))
            )
            and (not query.tags or bool(set(record.tags) & set(query.tags)))
            and record.importance >= query.min_importance
            and (query.before_step is None or record.step < query.before_step)
            and (
                query.content_locale is None
                or record.content_locale == query.content_locale
            )
        )

    def retrieve(self, query: MemoryQuery) -> Sequence[MemoryHit]:
        values = self._bank.retrieve_associative(
            query.query_text,
            max(query.limit * 4, query.limit),
        )
        records = [
            record
            for record in self._decode_many(values)
            if self._matches(record, query)
        ]
        if not records:
            return ()
        query_vector = self._embed(query.query_text)
        reference_step = query.before_step or max(record.step for record in records) + 1

        def relation_score(record: MemoryRecord) -> float:
            scores: list[float] = []
            for requested, actual in (
                (query.actor_ids, record.actor_ids),
                (query.location_ids, record.location_ids),
                (query.tags, record.tags),
            ):
                if requested:
                    scores.append(
                        len(set(requested) & set(actual)) / len(set(requested))
                    )
            return sum(scores) / len(scores) if scores else 0.5

        ranked: list[MemoryHit] = []
        for record in records:
            record_vector = self._embed(record.raw_text or self._codec.encode(record))
            if record_vector.shape != query_vector.shape:
                raise ValueError(
                    "memory hash vectors changed dimensions within one bank"
                )
            cosine = float(np.dot(query_vector, record_vector))
            semantic = min(1.0, max(0.0, (cosine + 1.0) / 2.0))
            distance = max(0, reference_step - record.step)
            recency = math.exp(-distance / 50.0)
            relation = relation_score(record)
            score = (
                0.55 * semantic
                + 0.20 * record.importance
                + 0.15 * recency
                + 0.10 * relation
            )
            ranked.append(
                MemoryHit(
                    record=record,
                    score=score,
                    semantic_score=semantic,
                    recency_score=recency,
                    importance_score=record.importance,
                )
            )
        ranked.sort(
            key=lambda hit: (hit.score, hit.record.step, hit.record.record_id),
            reverse=True,
        )
        return tuple(ranked[: query.limit])

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

    def flush(self) -> None:
        self._bank.get_data_frame()

    def snapshot(self) -> MemorySnapshot:
        state = cast(dict[str, Any], self._bank.get_state())
        serialized = json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return MemorySnapshot(
            owner_id=self._owner_id,
            scope=self._scope,
            state=state,
            record_count=len(self._bank),
            state_hash=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        )

    def restore(self, snapshot: MemorySnapshot) -> None:
        if snapshot.owner_id != self._owner_id or snapshot.scope != self._scope:
            raise ValueError("memory snapshot owner or scope does not match bank")
        serialized = json.dumps(
            snapshot.state,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        if digest != snapshot.state_hash:
            raise ValueError("memory snapshot hash mismatch")
        self._bank.set_state(cast(dict[str, Any], snapshot.state))
        self._vector_cache.clear()
        if len(self._bank) != snapshot.record_count:
            raise ValueError("memory snapshot record count mismatch")
