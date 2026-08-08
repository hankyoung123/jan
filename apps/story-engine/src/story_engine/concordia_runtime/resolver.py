import hashlib
import json
import re
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
from story_engine.domain.simulation import (
    CharacterRef,
    GameMasterActor,
    ResolverContext,
)


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
                f"- {character.display_name}, {type_labels[character.type]}{location}"
            )
        lines.append(
            "Use these exact display names in participant_names or observer_names. "
            "Do not include an existing character in entity_changes."
        )
        return "\n".join(lines)

    @staticmethod
    def _characters_by_name(context: ResolverContext) -> dict[str, CharacterRef]:
        characters_by_name: dict[str, CharacterRef] = {}
        for character in context.existing_characters:
            name_key = character.display_name.strip().casefold()
            if name_key in characters_by_name:
                raise ResolutionEnvelopeError(
                    "existing character display names must be unique for "
                    "semantic GM resolution"
                )
            characters_by_name[name_key] = character
        return characters_by_name

    @classmethod
    def _resolve_character_names(
        cls,
        names: tuple[str, ...],
        *,
        context: ResolverContext,
        field_name: str,
    ) -> tuple[str, ...]:
        characters_by_name = cls._characters_by_name(context)
        resolved_ids: list[str] = []
        unknown_names: list[str] = []
        for name in names:
            character = characters_by_name.get(name.strip().casefold())
            if character is None:
                unknown_names.append(name)
            else:
                resolved_ids.append(character.id)
        if unknown_names:
            raise ResolutionEnvelopeError(
                f"Game Master resolution references unknown {field_name}: "
                f"{sorted(unknown_names)}"
            )
        return tuple(dict.fromkeys(resolved_ids))

    @staticmethod
    def _normalized_entity_changes(
        envelope: ResolutionEnvelope,
        context: ResolverContext,
    ) -> tuple[tuple[tuple[str, EntityChange], ...], tuple[str, ...]]:
        unique_changes: dict[str, EntityChange] = {}
        existing_by_name = ConcordiaResolverKernel._characters_by_name(context)
        for change in envelope.entity_changes:
            name_key = (change.display_name or "").strip().casefold()
            previous = unique_changes.get(name_key)
            if previous is not None and previous != change:
                raise ResolutionEnvelopeError(
                    "Game Master resolution contains conflicting create_npc "
                    f"definitions for {change.display_name!r}"
                )
            unique_changes[name_key] = change

        existing_by_id = {
            character.id: character for character in context.existing_characters
        }
        creations: list[tuple[str, EntityChange]] = []
        existing_references: list[str] = []
        used_ids = set(existing_by_id)
        for change in unique_changes.values():
            name_key = (change.display_name or "").strip().casefold()
            existing = existing_by_name.get(name_key)
            if existing is None:
                base = re.sub(r"[^a-z0-9]+", "-", name_key).strip("-")
                if not base:
                    base = hashlib.sha256(name_key.encode("utf-8")).hexdigest()[:12]
                entity_id = base
                suffix = 2
                while entity_id in used_ids:
                    entity_id = f"{base}-{suffix}"
                    suffix += 1
                used_ids.add(entity_id)
                creations.append((entity_id, change))
                continue
            existing_references.append(existing.id)
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

        acting_character = next(
            (
                character
                for character in context.existing_characters
                if character.id == context.acting_actor_id
            ),
            None,
        )
        if acting_character is None:
            raise ResolutionEnvelopeError("acting character is not in the registry")
        observer_ids = ConcordiaResolverKernel._resolve_character_names(
            envelope.observer_names,
            context=context,
            field_name="observer names",
        )
        if envelope.visibility == EventVisibility.RESTRICTED and not observer_ids:
            observer_ids = (context.acting_actor_id,)
        non_actor_observers = set(observer_ids) - {
            character.id
            for character in context.existing_characters
            if character.type == "active"
        }
        if non_actor_observers:
            raise ResolutionEnvelopeError(
                "Game Master resolution uses non-active observer names: "
                f"{sorted(non_actor_observers)}"
            )
        entity_changes, existing_references = (
            ConcordiaResolverKernel._normalized_entity_changes(envelope, context)
        )
        participant_ids = tuple(
            dict.fromkeys(
                (
                    *ConcordiaResolverKernel._resolve_character_names(
                        envelope.participant_names or (acting_character.display_name,),
                        context=context,
                        field_name="participant names",
                    ),
                    *existing_references,
                )
            )
        )
        participant_ids = tuple(
            dict.fromkeys(
                (
                    *participant_ids,
                    *(entity_id for entity_id, _ in entity_changes),
                )
            )
        )
        effects: list[StateEffect] = []
        for index, (entity_id, change) in enumerate(entity_changes):
            effects.append(
                StateEffect(
                    effect_id=(
                        f"entity-effect:{context.session_id}:{context.step}:{index}"
                    ),
                    operation=EffectOperation.CREATE_CHARACTER,
                    target=EffectTarget.CHARACTER_PROJECTION,
                    target_id=entity_id,
                    after=(
                        {
                            "entity_id": entity_id,
                            "display_name": change.display_name,
                            "identity": change.identity,
                            "core_desire": change.core_desire,
                            "location": change.location,
                        }
                    ),
                    reason_text=envelope.event_text,
                )
            )
        for index, update in enumerate(envelope.state_updates):
            target_id: str | None = None
            if update.target == EffectTarget.CHARACTER_PROJECTION:
                assert update.target_name is not None
                target_ids = ConcordiaResolverKernel._resolve_character_names(
                    (update.target_name,),
                    context=context,
                    field_name="state update target",
                )
                target_id = target_ids[0]
            effects.append(
                StateEffect(
                    effect_id=(
                        f"state-effect:{context.session_id}:{context.step}:{index}"
                    ),
                    operation=EffectOperation.SET,
                    target=update.target,
                    target_id=target_id,
                    path=update.path,
                    after=update.value,
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
                    "Treat the actor text as a putative intent, never a fact or "
                    "guaranteed outcome. Resolve only from committed world facts, "
                    "actor state, available resources, environmental conditions, "
                    "other actors, time, and world rules."
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
