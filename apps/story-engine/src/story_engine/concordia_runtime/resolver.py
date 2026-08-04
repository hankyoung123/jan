import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from threading import Event

from pydantic import ValidationError

from story_engine.concordia_runtime.memory import ConcordiaMemoryCodec
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.memory import MemoryRecord, MemoryRecordType, MemoryScope
from story_engine.domain.projection import (
    EffectOperation,
    EffectTarget,
    EventVisibility,
    ResolutionEnvelope,
    ResolvedEvent,
    ResolvedTurn,
    SimulationBoundary,
    StateEffect,
)
from story_engine.domain.simulation import GameMasterActor, ResolverContext


class SimulationCancelledError(RuntimeError):
    """Raised before an unresolved step can advance persistent state."""


class ResolutionEnvelopeError(ValueError):
    """Raised when a Game Master resolution is not a usable JSON envelope."""


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
        except json.JSONDecodeError as error:
            raise ResolutionEnvelopeError(
                "Game Master resolution must be valid JSON"
            ) from error
        if not isinstance(payload, Mapping):
            raise ResolutionEnvelopeError(
                "Game Master resolution JSON must be an object"
            )
        try:
            envelope = ResolutionEnvelope.model_validate(payload)
        except ValidationError as error:
            location = ".".join(str(item) for item in error.errors()[0]["loc"])
            raise ResolutionEnvelopeError(
                f"Game Master resolution JSON failed schema validation at {location}"
            ) from error

        observer_ids = envelope.observer_ids
        if envelope.visibility == EventVisibility.RESTRICTED and not observer_ids:
            observer_ids = (context.acting_actor_id,)
        participant_ids = envelope.participant_ids or (context.acting_actor_id,)
        effects: list[StateEffect] = []
        for index, change in enumerate(envelope.entity_changes):
            effects.append(
                StateEffect(
                    effect_id=(
                        f"entity-effect:{context.session_id}:{context.step}:{index}"
                    ),
                    operation=EffectOperation.CREATE_CHARACTER,
                    target=EffectTarget.CHARACTER_PROJECTION,
                    target_id=change.entity_id,
                    after=(
                        {
                            "entity_id": change.entity_id,
                            "display_name": change.display_name,
                            "identity": change.identity,
                            "core_desire": change.core_desire,
                            "location": change.location,
                        }
                    ),
                    reason_text=envelope.event_text,
                )
            )
        event_text = envelope.event_text.strip()
        event = ResolvedEvent(
            event_id=f"event:{context.session_id}:{context.step}",
            session_id=context.session_id,
            step=context.step,
            actor_id=context.acting_actor_id,
            event_text=event_text,
            visibility=envelope.visibility,
            observer_ids=observer_ids,
            participant_ids=participant_ids,
            source_intent_ids=(f"putative:{context.session_id}:{context.step}",),
            effects=tuple(effects),
            content_locale=context.content_locale,
            occurred_at=datetime.now(UTC),
        )
        return event_text, envelope.boundary, (event,), tuple(effects)

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
