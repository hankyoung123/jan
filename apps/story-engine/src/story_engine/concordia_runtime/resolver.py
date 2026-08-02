import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from threading import Event

from story_engine.concordia_runtime.memory import ConcordiaMemoryCodec
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.projection import (
    EffectOperation,
    EffectTarget,
    EventVisibility,
    ResolvedEvent,
    ResolvedTurn,
    SimulationBoundary,
    StateEffect,
)
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

    @staticmethod
    def _resolution_envelope(
        raw: str,
        context: ResolverContext,
    ) -> tuple[
        str,
        SimulationBoundary | None,
        tuple[ResolvedEvent, ...],
        tuple[StateEffect, ...],
    ]:
        try:
            payload = json.loads(raw.strip().removeprefix("Event:").strip())
        except json.JSONDecodeError:
            return raw, None, (), ()
        if not isinstance(payload, Mapping):
            return raw, None, (), ()
        event_text = payload.get("event_text")
        if not isinstance(event_text, str) or not event_text.strip():
            return raw, None, (), ()
        try:
            boundary = SimulationBoundary(payload.get("boundary", "none"))
            visibility = EventVisibility(payload.get("visibility", "participants"))
        except ValueError:
            return raw, None, (), ()

        def string_tuple(value: object) -> tuple[str, ...]:
            if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
                return ()
            return tuple(item for item in value if isinstance(item, str))

        observer_ids = string_tuple(payload.get("observer_ids"))
        participant_ids = string_tuple(payload.get("participant_ids")) or (
            context.acting_actor_id,
        )
        if visibility == EventVisibility.RESTRICTED and not observer_ids:
            observer_ids = (context.acting_actor_id,)
        effects: list[StateEffect] = []
        changes = payload.get("entity_changes", ())
        if isinstance(changes, Sequence) and not isinstance(changes, (str, bytes)):
            for index, value in enumerate(changes):
                if not isinstance(value, Mapping):
                    continue
                operation = value.get("operation")
                entity_id = value.get("entity_id")
                if operation not in {"create", "archive"} or not isinstance(
                    entity_id, str
                ):
                    continue
                after = (
                    {
                        "entity_id": entity_id,
                        "display_name": value.get("display_name", entity_id),
                        "identity": value.get(
                            "identity", "A newly introduced character."
                        ),
                        "goal": value.get("goal", "Respond to the current situation."),
                        "location": value.get("location"),
                        "active": True,
                    }
                    if operation == "create"
                    else None
                )
                effects.append(
                    StateEffect(
                        effect_id=(
                            f"entity-effect:{context.session_id}:{context.step}:{index}"
                        ),
                        operation=(
                            EffectOperation.CREATE_ENTITY
                            if operation == "create"
                            else EffectOperation.ARCHIVE_ENTITY
                        ),
                        target=EffectTarget.CHARACTER_PROJECTION,
                        target_id=entity_id,
                        after=after,
                        reason_text=event_text,
                    )
                )
        event = ResolvedEvent(
            event_id=f"event:{context.session_id}:{context.step}",
            session_id=context.session_id,
            step=context.step,
            actor_id=context.acting_actor_id,
            event_text=event_text.strip(),
            visibility=visibility,
            observer_ids=observer_ids,
            participant_ids=participant_ids,
            source_intent_ids=(f"putative:{context.session_id}:{context.step}",),
            effects=tuple(effects),
            content_locale=context.content_locale,
            occurred_at=datetime.now(UTC),
        )
        return event_text.strip(), boundary, (event,), tuple(effects)

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
        event_text, boundary, events, effects = self._resolution_envelope(raw, context)
        if boundary is None:
            boundary = SimulationBoundary(
                game_master.act(
                    ActionSpec(
                        spec_id=f"boundary:{context.session_id}:{context.step}",
                        output_type=ActionOutputType.CHOICE,
                        call_to_action=(
                            "Classify the boundary created by this resolved event. "
                            "Choose chapter only for a completed chapter arc, "
                            "scene for "
                            "a natural scene transition, otherwise none."
                        ),
                        options=("none", "scene", "chapter"),
                        option_ids=("none", "scene", "chapter"),
                        tag="simulation_boundary",
                        content_locale=context.content_locale,
                    )
                )
                .strip()
                .casefold()
            )

        event = MemoryRecord(
            record_id=f"event:{context.session_id}:{context.step}",
            record_type=MemoryRecordType.WORLD_EVENT,
            scope=MemoryScope.GAME_MASTER,
            owner_id=game_master.name,
            session_id=context.session_id,
            branch_id=context.branch_id,
            step=context.step,
            text=event_text,
            content_locale=context.content_locale,
            created_at=datetime.now().astimezone(),
            actor_ids=(context.acting_actor_id,),
            source_record_ids=(putative.record_id,),
            tags=("event",),
        )
        game_master.observe(self._memory_codec.encode(event))

        if self._projector is not None:
            return self._projector(event_text, context)
        return ResolvedTurn(
            session_id=context.session_id,
            branch_id=context.branch_id,
            step=context.step,
            acting_actor_id=context.acting_actor_id,
            putative_event_text=context.putative_event_text,
            raw_resolution_text=event_text,
            events=events,
            effects=effects,
            content_locale=context.content_locale,
            boundary=boundary,
        )
