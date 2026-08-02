from collections.abc import Callable
from datetime import datetime
from threading import Event

from story_engine.concordia_runtime.memory import ConcordiaMemoryCodec
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.projection import ResolvedTurn
from story_engine.domain.simulation import GameMasterActor, ResolverContext


class SimulationCancelledError(RuntimeError):
    """Raised before an unresolved step can advance persistent state."""


class ConcordiaResolverKernel:
    """Resolve putative actor actions through the persistent Game Master."""

    def __init__(
        self,
        *,
        memory_codec: ConcordiaMemoryCodec | None = None,
        projector: Callable[[str, ResolverContext], ResolvedTurn] | None = None,
    ) -> None:
        self._memory_codec = memory_codec or ConcordiaMemoryCodec()
        self._projector = projector

    def resolve(
        self,
        game_master: GameMasterActor,
        context: ResolverContext,
        *,
        cancellation: Event,
    ) -> ResolvedTurn:
        if cancellation.is_set():
            raise SimulationCancelledError("simulation was cancelled")

        putative = MemoryRecord(
            record_id=f"putative:{context.session_id}:{context.step}",
            record_type=MemoryRecordType.PUTATIVE_EVENT,
            scope=MemoryScope.GAME_MASTER,
            owner_id=game_master.name,
            session_id=context.session_id,
            branch_id=context.branch_id,
            step=context.step,
            text=context.putative_event_text,
            content_locale=context.content_locale,
            created_at=datetime.now().astimezone(),
            actor_ids=(context.acting_actor_id,),
            tags=("putative_event",),
        )
        game_master.observe(self._memory_codec.encode(putative))
        raw = game_master.act(
            ActionSpec(
                spec_id=f"resolve:{context.session_id}:{context.step}",
                output_type=ActionOutputType.RESOLVE,
                call_to_action=(
                    "Considering all established facts, what actually happens?"
                ),
                tag="resolve",
                content_locale=context.content_locale,
            )
        )
        if cancellation.is_set():
            raise SimulationCancelledError("simulation was cancelled")

        event = MemoryRecord(
            record_id=f"event:{context.session_id}:{context.step}",
            record_type=MemoryRecordType.WORLD_EVENT,
            scope=MemoryScope.GAME_MASTER,
            owner_id=game_master.name,
            session_id=context.session_id,
            branch_id=context.branch_id,
            step=context.step,
            text=raw,
            content_locale=context.content_locale,
            created_at=datetime.now().astimezone(),
            actor_ids=(context.acting_actor_id,),
            source_record_ids=(putative.record_id,),
            tags=("event",),
        )
        game_master.observe(self._memory_codec.encode(event))

        if self._projector is not None:
            return self._projector(raw, context)
        return ResolvedTurn(
            session_id=context.session_id,
            branch_id=context.branch_id,
            step=context.step,
            acting_actor_id=context.acting_actor_id,
            putative_event_text=context.putative_event_text,
            raw_resolution_text=raw,
            content_locale=context.content_locale,
        )
