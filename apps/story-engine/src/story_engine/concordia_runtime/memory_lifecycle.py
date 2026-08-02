from datetime import UTC, datetime

from story_engine.concordia_runtime.factory import (
    ConcordiaGameMasterActor,
    ConcordiaStoryActor,
)
from story_engine.concordia_runtime.language_model import JanConcordiaLanguageModel
from story_engine.domain.memory import MemoryRecord, MemoryRecordType
from story_engine.domain.projection import SimulationBoundary


class ConcordiaMemoryLifecycle:
    """Create durable reflections and compact long-running memory at boundaries."""

    def __init__(
        self,
        *,
        reflection_model: JanConcordiaLanguageModel,
        consolidation_model: JanConcordiaLanguageModel,
        content_locale: str,
    ) -> None:
        self._reflection_model = reflection_model
        self._consolidation_model = consolidation_model
        self._content_locale = content_locale

    def set_content_locale(self, content_locale: str) -> None:
        self._content_locale = content_locale

    @staticmethod
    def _recent_text(actor: ConcordiaStoryActor) -> tuple[str, tuple[str, ...]]:
        records = tuple(actor.memory.retrieve_recent(limit=16))
        return (
            "\n".join(f"- {record.text}" for record in records),
            tuple(record.record_id for record in records),
        )

    def _write(
        self,
        *,
        actor: ConcordiaStoryActor,
        session_id: str,
        branch_id: str,
        step: int,
        record_type: MemoryRecordType,
        text: str,
        source_ids: tuple[str, ...],
    ) -> MemoryRecord:
        record = MemoryRecord(
            record_id=f"{record_type.value}:{session_id}:{step}:{actor.name}",
            record_type=record_type,
            scope=actor.memory.scope,
            owner_id=actor.name,
            session_id=session_id,
            branch_id=branch_id,
            step=step,
            text=text,
            content_locale=self._content_locale,
            created_at=datetime.now(UTC),
            actor_ids=()
            if isinstance(actor, ConcordiaGameMasterActor)
            else (actor.name,),
            source_record_ids=source_ids,
            visible_to=()
            if isinstance(actor, ConcordiaGameMasterActor)
            else (actor.name,),
            tags=(record_type.value,),
            importance=0.85 if record_type == MemoryRecordType.REFLECTION else 0.75,
        )
        actor.memory.add(record)
        return record

    def process_boundary(
        self,
        *,
        session_id: str,
        branch_id: str,
        step: int,
        boundary: SimulationBoundary,
        acting_actor: ConcordiaStoryActor,
        game_master: ConcordiaGameMasterActor,
    ) -> tuple[MemoryRecord, ...]:
        if boundary == SimulationBoundary.NONE:
            return ()
        written: list[MemoryRecord] = []
        for subject in (game_master, acting_actor):
            recent, source_ids = self._recent_text(subject)
            reflection = self._reflection_model.sample_text(
                "Summarize the causal lesson, unresolved tension, and likely next "
                f"goal from these memories. Do not invent facts. Respond in "
                f"{self._content_locale}.\n{recent}",
                max_tokens=512,
                terminators=(),
                temperature=0.2,
            ).strip()
            if reflection:
                written.append(
                    self._write(
                        actor=subject,
                        session_id=session_id,
                        branch_id=branch_id,
                        step=step,
                        record_type=MemoryRecordType.REFLECTION,
                        text=reflection,
                        source_ids=source_ids,
                    )
                )

        should_consolidate = boundary == SimulationBoundary.CHAPTER or step % 10 == 0
        if should_consolidate:
            for subject in (game_master, acting_actor):
                recent, source_ids = self._recent_text(subject)
                consolidated = self._consolidation_model.sample_text(
                    "Compress these memories into stable facts and open threads. "
                    "Preserve uncertainty and privacy; do not add facts. Respond in "
                    f"{self._content_locale}.\n{recent}",
                    max_tokens=768,
                    terminators=(),
                    temperature=0.1,
                ).strip()
                if consolidated:
                    written.append(
                        self._write(
                            actor=subject,
                            session_id=session_id,
                            branch_id=branch_id,
                            step=step,
                            record_type=MemoryRecordType.CONSOLIDATION,
                            text=consolidated,
                            source_ids=source_ids,
                        )
                    )
        return tuple(written)
