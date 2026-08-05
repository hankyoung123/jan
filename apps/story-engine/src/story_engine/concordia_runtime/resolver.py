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
    EntityChange,
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
    def _existing_characters_prompt(context: ResolverContext) -> str:
        type_labels = {
            "active": "active character",
            "npc": "ordinary NPC",
            "retired": "retired character",
        }
        lines: list[str] = []
        for character in context.existing_characters:
            location = f", location: {character.location}" if character.location else ""
            lines.append(
                f"- {character.id}: {character.display_name}, "
                f"{type_labels[character.type]}{location}"
            )
        lines.append(
            "Existing IDs must be referenced through participant_ids and must not "
            "appear in entity_changes."
        )
        return "\n".join(lines)

    @staticmethod
    def _normalized_entity_changes(
        envelope: ResolutionEnvelope,
        context: ResolverContext,
    ) -> tuple[tuple[EntityChange, ...], tuple[str, ...]]:
        unique_changes: dict[str, EntityChange] = {}
        for change in envelope.entity_changes:
            previous = unique_changes.get(change.entity_id)
            if previous is not None and previous != change:
                raise ResolutionEnvelopeError(
                    "Game Master resolution contains conflicting create_npc "
                    f"definitions for entity_id {change.entity_id!r}"
                )
            unique_changes[change.entity_id] = change

        existing_by_id = {
            character.id: character for character in context.existing_characters
        }
        creations: list[EntityChange] = []
        existing_references: list[str] = []
        for change in unique_changes.values():
            existing = existing_by_id.get(change.entity_id)
            if existing is None:
                creations.append(change)
                continue
            if existing.display_name.casefold() != str(change.display_name).casefold():
                raise ResolutionEnvelopeError(
                    f"entity_id collision for {change.entity_id!r}: existing character "
                    f"is named {existing.display_name!r}, but create_npc used "
                    f"{change.display_name!r}"
                )
            existing_references.append(change.entity_id)
        return tuple(creations), tuple(existing_references)

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
        entity_changes, existing_references = (
            ConcordiaResolverKernel._normalized_entity_changes(envelope, context)
        )
        participant_ids = tuple(
            dict.fromkeys(
                (
                    *(envelope.participant_ids or (context.acting_actor_id,)),
                    *existing_references,
                )
            )
        )
        known_character_ids = {
            *(character.id for character in context.existing_characters),
            *(change.entity_id for change in entity_changes),
        }
        unknown_participants = set(participant_ids) - known_character_ids
        if unknown_participants:
            raise ResolutionEnvelopeError(
                "Game Master resolution references unknown participant IDs: "
                f"{sorted(unknown_participants)}"
            )
        effects: list[StateEffect] = []
        for index, change in enumerate(entity_changes):
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
        game_master.set_resolution_character_registry(
            self._existing_characters_prompt(context)
        )
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
