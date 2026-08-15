import hashlib
import re
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import cast

from pydantic import JsonValue

from story_engine.concordia_runtime.factory import (
    ConcordiaGameMasterActor,
    ConcordiaStoryActor,
)
from story_engine.concordia_runtime.memory import ConcordiaMemoryBank
from story_engine.concordia_runtime.resolver import (
    ConcordiaResolverKernel,
    SimulationCancelledError,
)
from story_engine.concordia_runtime.roster import (
    MAX_SCENE_ROSTER_SIZE,
    ConcordiaRosterPlanner,
)
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.memory import MemoryRecord, MemoryRecordType
from story_engine.domain.models import Character, Fact, FactVisibility, WorldState
from story_engine.domain.projection import (
    EffectOperation,
    EffectTarget,
    EventVisibility,
    ResolvedEvent,
    ResolvedTurn,
    SimulationBoundary,
    StateEffect,
)
from story_engine.domain.recipe import PerceptionFrame
from story_engine.domain.simulation import (
    ActorStateContext,
    InitiativeContext,
    InitiativeTrigger,
    ResolverContext,
    StepResult,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.domain.trace import (
    ModelCallTrace,
    SimulationObserver,
    SimulationStage,
    SimulationStageEvent,
    StageStatus,
)


@dataclass(frozen=True, slots=True)
class _MutationSnapshot:
    characters: dict[str, Character]
    world: WorldState | None
    pending_scene_events: tuple[ResolvedEvent, ...]
    actor_states: dict[str, dict[str, JsonValue]]
    game_master_state: dict[str, JsonValue] | None
    memory_records: dict[str, tuple[MemoryRecord, ...]]
    turns_without_material_world_change: int
    turns_since_last_initiative: int
    handled_clock_ids: frozenset[str]


class StorySimulationRuntime:
    """One branch-local set of persistent Concordia entities and memories."""

    def __init__(
        self,
        *,
        project_id: str,
        session_id: str,
        branch_id: str,
        content_locale: str,
        actors: tuple[ConcordiaStoryActor, ...],
        game_master: ConcordiaGameMasterActor,
        resolver: ConcordiaResolverKernel | None = None,
        cancellation: Event | None = None,
        model_traces: list[ModelCallTrace] | None = None,
        initial_snapshot: TurnSessionSnapshot | None = None,
        language_models: Sequence[object] = (),
        observer: SimulationObserver | None = None,
        characters: tuple[Character, ...] = (),
        canonical_facts: tuple[Fact, ...] = (),
        world: WorldState | None = None,
        project_root: Path | None = None,
        player_actor_id: str | None = None,
        pending_scene_events: tuple[ResolvedEvent, ...] = (),
        game_master_rebuilder: Callable[
            [tuple[ConcordiaStoryActor, ...], ConcordiaGameMasterActor],
            ConcordiaGameMasterActor,
        ]
        | None = None,
        available_actors: tuple[ConcordiaStoryActor, ...] = (),
        roster_planner: ConcordiaRosterPlanner | None = None,
        initial_roster_selected: bool = False,
    ) -> None:
        if not actors:
            raise ValueError("simulation runtime requires at least one actor")
        if len(actors) > MAX_SCENE_ROSTER_SIZE:
            raise ValueError(
                f"scene roster cannot exceed {MAX_SCENE_ROSTER_SIZE} active Agents"
            )
        self.project_id = project_id
        self.session_id = session_id
        self.branch_id = branch_id
        self.content_locale = content_locale
        self.actors = actors
        self.game_master = game_master
        self.resolver = resolver or ConcordiaResolverKernel()
        self.cancellation = cancellation or Event()
        self._model_traces = model_traces if model_traces is not None else []
        self.initial_snapshot = initial_snapshot
        self._language_models = list(language_models)
        self._observer = observer
        self._stage_events: list[SimulationStageEvent] = []
        self._actors_by_name = {actor.name: actor for actor in actors}
        self._all_actors_by_name = {
            actor.name: actor for actor in (*actors, *available_actors)
        }
        self._characters_by_id = {character.id: character for character in characters}
        self._canonical_facts = canonical_facts
        self._world = world
        self._project_root = project_root
        self.player_actor_id = player_actor_id
        self._pending_scene_events = list(pending_scene_events)
        self._game_master_rebuilder = game_master_rebuilder
        self._roster_planner = roster_planner
        self._roster_planned = initial_snapshot is not None or initial_roster_selected
        self._turns_without_material_world_change = (
            initial_snapshot.turns_without_material_world_change
            if initial_snapshot is not None
            else 0
        )
        self._turns_since_last_initiative = (
            initial_snapshot.turns_since_last_initiative
            if initial_snapshot is not None
            else 2
        )
        self._handled_clock_ids = set(
            initial_snapshot.handled_clock_ids if initial_snapshot is not None else ()
        )
        if len(self._actors_by_name) != len(actors):
            raise ValueError("simulation actor IDs must be unique")
        if (
            self.player_actor_id is not None
            and self.player_actor_id not in self._actors_by_name
        ):
            raise ValueError("interactive scene roster must include the player")

    def roster_actor_ids(self) -> tuple[str, ...]:
        return tuple(actor.name for actor in self.actors)

    @staticmethod
    def _location_memory_id(value: str) -> str:
        normalized = value.strip().casefold()
        digest = hashlib.sha256(normalized.encode()).hexdigest()[:24]
        return f"location:{digest}"

    def _memory_location_ids(
        self,
        actor_id: str,
        event_location_ids: tuple[str, ...] = (),
    ) -> tuple[str, ...]:
        locations = list(event_location_ids)
        character = self._characters_by_id.get(actor_id)
        if character is not None and character.location:
            locations.append(self._location_memory_id(character.location))
        if self._world is not None and self._world.current_location:
            locations.append(self._location_memory_id(self._world.current_location))
        return tuple(dict.fromkeys(locations))

    def _memory_participant_ids(self, actor_id: str) -> tuple[str, ...]:
        participants: list[str] = []
        for event in self._pending_scene_events[-4:]:
            visible = (
                event.visibility == EventVisibility.PUBLIC
                or actor_id == event.actor_id
                or actor_id in event.participant_ids
                or actor_id in event.observer_ids
            )
            if visible:
                participants.extend(event.participant_ids)
                if event.actor_id is not None:
                    participants.append(event.actor_id)
        return tuple(dict.fromkeys(participants))

    def character_states(self) -> tuple[Character, ...]:
        return tuple(
            self._characters_by_id[key] for key in sorted(self._characters_by_id)
        )

    def world_state(self) -> WorldState | None:
        return self._world

    def pending_scene_events(self) -> tuple[ResolvedEvent, ...]:
        return tuple(self._pending_scene_events)

    def initiative_state(self) -> tuple[int, int, tuple[str, ...]]:
        return (
            self._turns_without_material_world_change,
            self._turns_since_last_initiative,
            tuple(sorted(self._handled_clock_ids)),
        )

    def _capture_mutation_snapshot(self) -> _MutationSnapshot:
        return _MutationSnapshot(
            characters=dict(self._characters_by_id),
            world=self._world,
            pending_scene_events=tuple(self._pending_scene_events),
            actor_states=self.actor_states(),
            game_master_state=(
                self.game_master.get_state()
                if callable(getattr(self.game_master, "get_state", None))
                else None
            ),
            memory_records={
                owner_id: bank.records()
                for owner_id, bank in self._memory_banks().items()
            },
            turns_without_material_world_change=(
                self._turns_without_material_world_change
            ),
            turns_since_last_initiative=self._turns_since_last_initiative,
            handled_clock_ids=frozenset(self._handled_clock_ids),
        )

    def _restore_mutation_snapshot(self, snapshot: _MutationSnapshot) -> None:
        self._characters_by_id = dict(snapshot.characters)
        self._world = snapshot.world
        self._pending_scene_events = list(snapshot.pending_scene_events)
        for actor_id, state in snapshot.actor_states.items():
            self._all_actors_by_name[actor_id].set_state(state)
        if snapshot.game_master_state is not None:
            self.game_master.set_state(snapshot.game_master_state)
        for owner_id, records in snapshot.memory_records.items():
            bank = self._memory_banks().get(owner_id)
            if bank is not None:
                bank.replace(records)
        self._turns_without_material_world_change = (
            snapshot.turns_without_material_world_change
        )
        self._turns_since_last_initiative = snapshot.turns_since_last_initiative
        self._handled_clock_ids = set(snapshot.handled_clock_ids)

    @staticmethod
    def _clock_minutes(value: object) -> int | None:
        if not isinstance(value, str):
            return None
        match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", value.strip())
        if match is None:
            return None
        return int(match.group(1)) * 60 + int(match.group(2))

    def _resolver_context(
        self,
        *,
        step: int,
        acting_actor_id: str,
        putative_event_text: str,
    ) -> ResolverContext:
        world = self._world
        available_actor_facts = self._actor_known_facts(acting_actor_id)
        recent_events = self._recent_scene_event_texts()
        relevant_facts = self._relevant_canonical_facts(
            putative_event_text=putative_event_text,
            recent_events=recent_events,
            actor_known_facts=available_actor_facts,
        )
        available_actor_fact_ids = {fact.id for fact in available_actor_facts}
        actor_known_facts = tuple(
            fact for fact in relevant_facts if fact.id in available_actor_fact_ids
        )
        return ResolverContext(
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            acting_actor_id=acting_actor_id,
            putative_event_text=putative_event_text,
            content_locale=self.content_locale,
            existing_characters=tuple(
                ActorStateContext.from_character(character)
                for character in self.character_states()
            ),
            world_time=world.current_time if world is not None else None,
            world_location=world.current_location if world is not None else None,
            world_rules=world.rules if world is not None else (),
            relevant_canonical_facts=relevant_facts,
            actor_known_facts=actor_known_facts,
            recent_scene_events=recent_events,
        )

    def _initiative_context(
        self,
        *,
        step: int,
        trigger: InitiativeTrigger,
    ) -> InitiativeContext:
        world = self._world
        return InitiativeContext(
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            content_locale=self.content_locale,
            trigger=trigger,
            existing_characters=tuple(
                ActorStateContext.from_character(character)
                for character in self.character_states()
            ),
            world_time=world.current_time if world is not None else None,
            world_location=world.current_location if world is not None else None,
            world_rules=world.rules if world is not None else (),
            active_pressures=world.active_pressures if world is not None else (),
            clocks=world.clocks if world is not None else (),
            world_variables=(
                cast(dict[str, JsonValue], dict(world.world_variables))
                if world is not None
                else {}
            ),
            recent_causal_events=self._recent_scene_event_texts(),
        )

    def world_initiative_trigger(
        self,
        *,
        pending_response_actor_ids: tuple[str, ...] = (),
    ) -> InitiativeTrigger | None:
        """Return the highest-priority deterministic trigger, if unblocked."""
        if pending_response_actor_ids or self._turns_since_last_initiative < 2:
            return None
        world = self._world
        if world is not None:
            now = self._clock_minutes(world.current_time)
            for clock in world.clocks:
                if clock.id in self._handled_clock_ids:
                    continue
                due = self._clock_minutes(clock.due_at)
                if (now is not None and due is not None and due <= now) or (
                    now is None and clock.due_at == world.current_time
                ):
                    return InitiativeTrigger(
                        reason="due_clock",
                        source_id=clock.id,
                        description=clock.description,
                    )
            if world.active_pressures:
                return InitiativeTrigger(
                    reason="due_pressure",
                    source_id="pressure:0",
                    description=world.active_pressures[0],
                )
        if self._turns_without_material_world_change >= 3:
            return InitiativeTrigger(
                reason="stagnation",
                description=(
                    "Three committed actor turns produced no material world change."
                ),
            )
        return None

    def record_committed_step(self, result: StepResult) -> None:
        """Advance deterministic initiative counters with the core step state."""
        if result.resolved_turn is None:
            if result.acting_actor_id is not None:
                self._turns_without_material_world_change += 1
                self._turns_since_last_initiative += 1
            return
        if result.acting_actor_id is None:
            self._turns_without_material_world_change = 0
            self._turns_since_last_initiative = 0
            return
        effects = (
            *result.resolved_turn.effects,
            *(
                effect
                for event in result.resolved_turn.events
                for effect in event.effects
            ),
        )
        material = any(
            effect.target == EffectTarget.WORLD_PROJECTION
            or effect.operation == EffectOperation.CREATE_CHARACTER
            or (
                effect.target == EffectTarget.CHARACTER_PROJECTION
                and effect.path in {"location", "conditions", "resources"}
            )
            for effect in effects
        )
        self._turns_without_material_world_change = (
            0 if material else self._turns_without_material_world_change + 1
        )
        self._turns_since_last_initiative += 1

    @staticmethod
    def _search_terms(text: str) -> set[str]:
        normalized = text.casefold()
        terms = set(re.findall(r"[a-z0-9][a-z0-9_-]+", normalized))
        for chunk in re.findall(r"[\u3400-\u9fff]+", normalized):
            if len(chunk) <= 3:
                terms.add(chunk)
            terms.update(chunk[index : index + 2] for index in range(len(chunk) - 1))
        return terms

    def _actor_known_facts(self, actor_id: str) -> tuple[Fact, ...]:
        actor = self._characters_by_id[actor_id]
        known_ids = set(actor.known_fact_ids)
        return tuple(
            fact
            for fact in self._canonical_facts
            if fact.visibility == "public" or fact.id in known_ids
        )

    def _visible_scene_events(self, actor_id: str) -> tuple[ResolvedEvent, ...]:
        visible = tuple(
            event
            for event in self._pending_scene_events
            if event.visibility == EventVisibility.PUBLIC
            or actor_id == event.actor_id
            or actor_id in event.participant_ids
            or actor_id in event.observer_ids
        )
        return visible[-4:]

    def _recent_scene_event_texts(self) -> tuple[str, ...]:
        return tuple(event.event_text for event in self._pending_scene_events[-4:])

    def _perception_known_facts(
        self,
        actor_id: str,
        recent_events: tuple[ResolvedEvent, ...],
    ) -> tuple[Fact, ...]:
        actor = self._characters_by_id[actor_id]
        world = self._world
        context_text = "\n".join(
            (
                actor.display_name or actor.id,
                actor.location or "",
                world.current_location if world and world.current_location else "",
                world.scene_text if world is not None else "",
                *(event.event_text for event in recent_events),
            )
        )
        context_terms = self._search_terms(context_text)
        scored: list[tuple[int, int, Fact]] = []
        for index, fact in enumerate(self._actor_known_facts(actor_id)):
            overlap = len(context_terms & self._search_terms(fact.statement))
            if overlap:
                scored.append((overlap, -index, fact))
        scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
        return tuple(item[2] for item in scored[:8])

    def _perception_context_prompt(self, actor_id: str) -> str:
        actor = self._characters_by_id[actor_id]
        world = self._world
        recent_events = self._visible_scene_events(actor_id)
        known_facts = self._perception_known_facts(actor_id, recent_events)
        world_lines = [
            f"- Current time: {world.current_time if world else 'unknown'}",
            "- Current location: "
            + (
                world.current_location
                if world and world.current_location
                else "unknown"
            ),
            f"- Current scene: {world.scene_text if world else ''}",
        ]
        known_text = (
            "\n".join(f"- {fact.statement}" for fact in known_facts)
            or "- None directly relevant."
        )
        recent_text = (
            "\n".join(f"- {event.event_text}" for event in recent_events) or "- None."
        )
        return "\n\n".join(
            (
                "Current World State:\n" + "\n".join(world_lines),
                (
                    f"Actor Viewpoint and State ({actor.display_name or actor.id}):\n"
                    f"{ActorStateContext.from_character(actor).prompt_text()}"
                ),
                f"Actor Known Information:\n{known_text}",
                f"Recent Directly Relevant Events:\n{recent_text}",
            )
        )

    def _relevant_canonical_facts(
        self,
        *,
        putative_event_text: str,
        recent_events: tuple[str, ...],
        actor_known_facts: tuple[Fact, ...],
    ) -> tuple[Fact, ...]:
        context_text = "\n".join(
            (
                putative_event_text,
                *recent_events[-4:],
            )
        ).casefold()
        context_terms = self._search_terms(context_text)
        character_identity_terms: set[str] = set()
        for character in self._characters_by_id.values():
            character_identity_terms.update(self._search_terms(character.id))
            character_identity_terms.update(
                self._search_terms(character.display_name or character.id)
            )
        context_terms.difference_update(character_identity_terms)
        actor_known_ids = {fact.id for fact in actor_known_facts}
        scored: list[tuple[int, int, Fact]] = []
        for index, fact in enumerate(self._canonical_facts):
            fact_terms = self._search_terms(fact.statement)
            overlap = len(context_terms & fact_terms)
            if not overlap:
                continue
            score = overlap * 20
            if fact.id in actor_known_ids:
                score += 5
            scored.append((score, -index, fact))
        scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
        selected = tuple(item[2] for item in scored[:8])
        return selected

    @staticmethod
    def _collection_value(effect: StateEffect) -> tuple[str, ...]:
        """Materialize a collection already validated by its producing contract."""
        return tuple(cast(list[str], effect.after))

    @classmethod
    def _validate_resource_conservation(
        cls,
        effects: tuple[StateEffect, ...],
        characters: Mapping[str, Character],
    ) -> None:
        initial = {
            character_id: set(character.resources)
            for character_id, character in characters.items()
        }
        final = {
            character_id: set(resources) for character_id, resources in initial.items()
        }
        updated_characters: set[str] = set()
        for effect in effects:
            if (
                effect.operation != EffectOperation.SET
                or effect.target != EffectTarget.CHARACTER_PROJECTION
                or effect.path != "resources"
            ):
                continue
            if effect.target_id is None or effect.target_id not in characters:
                raise ValueError("character state update targets an unknown character")
            if effect.target_id in updated_characters:
                raise ValueError("resolution updates one character's resources twice")
            updated_characters.add(effect.target_id)
            final[effect.target_id] = set(cls._collection_value(effect))

        initial_holders: dict[str, set[str]] = {}
        final_holders: dict[str, set[str]] = {}
        for character_id, resources in initial.items():
            for resource in resources:
                initial_holders.setdefault(resource, set()).add(character_id)
        for character_id, resources in final.items():
            for resource in resources:
                final_holders.setdefault(resource, set()).add(character_id)

        materialized = set(final_holders) - set(initial_holders)
        if materialized:
            raise ValueError(
                f"resources cannot materialize from Resolution: {sorted(materialized)}"
            )
        for character_id, resources in final.items():
            for resource in resources - initial[character_id]:
                removed_by = initial_holders[resource] - final_holders[resource]
                if not removed_by:
                    raise ValueError(
                        "resource transfer must remove the resource from its "
                        f"previous holder before adding {resource!r}"
                    )
        for resource, holders in final_holders.items():
            if len(holders) > len(initial_holders.get(resource, ())):
                raise ValueError(f"resource transfer would duplicate {resource!r}")

    def _sync_actor_states(
        self,
        character_ids: set[str],
        *,
        characters: Mapping[str, Character] | None = None,
    ) -> None:
        source = characters or self._characters_by_id
        actor_backups: dict[str, dict[str, JsonValue]] = {}
        try:
            for character_id in sorted(character_ids):
                actor = self._all_actors_by_name.get(character_id)
                if actor is None:
                    continue
                actor_backups[character_id] = actor.get_state()
                actor.set_actor_state(
                    ActorStateContext.from_character(source[character_id]).prompt_text()
                )
        except Exception:
            for character_id, state in actor_backups.items():
                self._all_actors_by_name[character_id].set_state(state)
            raise

    def _apply_character_effects(self, resolved: ResolvedTurn) -> tuple[str, ...]:
        candidate_effects = (
            *resolved.effects,
            *(effect for event in resolved.events for effect in event.effects),
        )
        effects = tuple(
            {effect.effect_id: effect for effect in candidate_effects}.values()
        )
        changed: list[str] = []
        characters = dict(self._characters_by_id)
        new_characters: dict[str, Character] = {}
        for effect in effects:
            if effect.operation != EffectOperation.CREATE_CHARACTER:
                continue
            if effect.target_id is None or not isinstance(effect.after, dict):
                raise ValueError("create_character requires a target and payload")
            if effect.target_id in characters:
                raise ValueError(
                    "GM attempted to recreate existing character "
                    f"{effect.target_id!r}; reference it through participant_ids "
                    "instead."
                )
            if effect.target_id in new_characters:
                raise ValueError(
                    "GM attempted to create character "
                    f"{effect.target_id!r} more than once in one resolution"
                )
            payload = dict(effect.after)
            character = Character(
                id=effect.target_id,
                display_name=str(payload.get("display_name") or effect.target_id),
                type="npc",
                identity=str(payload.get("identity") or ""),
                core_desire=str(payload.get("core_desire") or ""),
                location=(
                    str(payload["location"]) if payload.get("location") else None
                ),
            )
            new_characters[character.id] = character
            changed.append(f"npc-created:{character.id}")

        characters.update(new_characters)
        character_ids = set(characters)
        actor_ids = set(self._actors_by_name)
        for event in resolved.events:
            unknown_participants = set(event.participant_ids) - character_ids
            if unknown_participants:
                raise ValueError(
                    "event contains unregistered participant IDs: "
                    f"{sorted(unknown_participants)}"
                )
            unknown_observers = set(event.observer_ids) - actor_ids
            if unknown_observers:
                raise ValueError(
                    "event contains non-actor observer IDs: "
                    f"{sorted(unknown_observers)}"
                )
            unknown_responders = set(event.response_actor_ids) - actor_ids
            if unknown_responders:
                raise ValueError(
                    "event contains non-actor response IDs: "
                    f"{sorted(unknown_responders)}"
                )
        self._validate_resource_conservation(effects, characters)
        changed_character_ids: set[str] = set()
        world = self._world
        for effect in effects:
            if effect.operation != EffectOperation.SET:
                continue
            if effect.target == EffectTarget.CHARACTER_PROJECTION:
                if effect.target_id is None or effect.path is None:
                    raise ValueError("character state update requires target and path")
                target_character = characters.get(effect.target_id)
                if target_character is None:
                    raise ValueError(
                        "character state update targets an unknown character"
                    )
                if effect.path in {"location", "current_goal"}:
                    update: dict[str, object] = {effect.path: effect.after}
                elif effect.path in {"conditions", "resources", "beliefs"}:
                    value = self._collection_value(effect)
                    update = {effect.path: value}
                else:
                    raise ValueError("character state update path is not supported")
                characters[target_character.id] = Character.model_validate(
                    target_character.model_copy(
                        update={
                            **update,
                            "version": target_character.version + 1,
                        }
                    )
                )
                changed_character_ids.add(target_character.id)
                changed.append(f"state-updated:{target_character.id}:{effect.path}")
                continue
            if effect.target == EffectTarget.WORLD_PROJECTION:
                if world is None or effect.path is None:
                    raise ValueError("world state update requires an initialized world")
                if effect.path not in {"current_time", "current_location"}:
                    raise ValueError("world state update path is not supported")
                if effect.path == "current_time":
                    previous_minutes = self._clock_minutes(world.current_time)
                    next_minutes = self._clock_minutes(effect.after)
                    if (
                        previous_minutes is not None
                        and next_minutes is not None
                        and next_minutes < previous_minutes
                    ):
                        raise ValueError("world time cannot move backwards")
                world_update: dict[str, object] = {
                    effect.path: effect.after,
                    "version": world.version + 1,
                }
                world = WorldState.model_validate(world.model_copy(update=world_update))
                changed.append(f"world-updated:{effect.path}")
                continue
            raise ValueError("state update targets unsupported projection")
        self._sync_actor_states(changed_character_ids, characters=characters)
        self._characters_by_id = characters
        self._world = world
        return tuple(changed)

    def _resolve_and_apply_atomically(
        self,
        resolve: Callable[[], ResolvedTurn],
        *,
        transform: Callable[[ResolvedTurn], ResolvedTurn] | None = None,
    ) -> tuple[ResolvedTurn, tuple[str, ...]]:
        """Rollback all authoritative runtime state when mutation cannot commit."""
        snapshot = self._capture_mutation_snapshot()
        try:
            resolved = resolve()
            if transform is not None:
                resolved = transform(resolved)
            changed = self._apply_character_effects(resolved)
        except Exception:
            self._restore_mutation_snapshot(snapshot)
            raise
        return resolved, changed

    def _advance_scene_boundary(
        self,
        resolved: ResolvedTurn,
        *,
        event_id: str,
        started_at: datetime,
        finalize: bool = True,
    ) -> None:
        self._pending_scene_events.extend(resolved.events)
        if not finalize or resolved.boundary == SimulationBoundary.NONE:
            return
        scene_events = tuple(self._pending_scene_events)
        self._pending_scene_events.clear()
        if self._roster_planner is None:
            return
        stage_event = self._publish_stage(
            step=resolved.step,
            stage=SimulationStage.ACTOR_SELECTION,
            status=StageStatus.RUNNING,
            started_at=started_at,
            input_record_ids=(event_id,),
        )
        self._set_trace_context(
            step=resolved.step,
            component_ids=("game-master:roster-selection",),
            source_record_ids=tuple(event.event_id for event in scene_events),
            stage=SimulationStage.ACTOR_SELECTION,
            task_label="下一场角色选择",
            stage_event_id=stage_event.event_id,
        )
        self._plan_next_roster(scene_events)
        self._publish_stage(
            step=resolved.step,
            stage=SimulationStage.ACTOR_SELECTION,
            status=StageStatus.SUCCEEDED,
            started_at=started_at,
            summary_text="Next scene roster selected; ordinary NPCs remain NPCs",
            input_record_ids=(event_id,),
        )

    def _response_npc_actor_ids(
        self,
        resolved: ResolvedTurn,
    ) -> tuple[str, ...]:
        """Return only explicitly assigned immediate voluntary responders."""
        response_actor_ids = {
            actor_id
            for event in resolved.events
            for actor_id in event.response_actor_ids
        }
        return tuple(
            actor.name
            for actor in self.actors
            if actor.name != self.player_actor_id and actor.name in response_actor_ids
        )

    def _active_roster_candidates(self) -> dict[str, tuple[str, str]]:
        return {
            character.id: (
                character.display_name or character.id,
                (
                    f"{character.identity}; goal: "
                    f"{character.current_goal or character.core_desire}; "
                    f"location: {character.location or 'unknown'}"
                ),
            )
            for character in self.character_states()
            if character.type == "active" and character.id in self._all_actors_by_name
        }

    def _replace_roster(self, selected: tuple[str, ...]) -> bool:
        if not selected:
            raise ValueError("scene roster requires at least one active Agent")
        if len(selected) > MAX_SCENE_ROSTER_SIZE:
            raise ValueError(
                f"scene roster cannot exceed {MAX_SCENE_ROSTER_SIZE} active Agents"
            )
        if self.player_actor_id is not None and self.player_actor_id not in selected:
            raise ValueError("interactive scene roster cannot exclude the player")
        unknown = set(selected) - set(self._all_actors_by_name)
        if unknown:
            raise ValueError(f"scene roster contains unknown Agents: {sorted(unknown)}")
        if selected == self.roster_actor_ids():
            return False
        if self._game_master_rebuilder is None:
            raise ValueError("Game Master roster rebuilder is unavailable")
        actors = tuple(self._all_actors_by_name[actor_id] for actor_id in selected)
        self.actors = actors
        self._actors_by_name = {actor.name: actor for actor in actors}
        self.game_master = self._game_master_rebuilder(actors, self.game_master)
        return True

    def _plan_initial_roster(self) -> tuple[str, ...]:
        if self._roster_planner is None or self._roster_planned:
            return ()
        candidates, _, minimum, maximum, required = self._roster_plan_inputs()
        selected = required + self._roster_planner.select_initial(
            candidates,
            min_count=minimum,
            max_count=maximum,
        )
        self._replace_roster(selected)
        self._roster_planned = True
        return selected

    def _plan_next_roster(
        self,
        scene_events: tuple[ResolvedEvent, ...],
    ) -> tuple[str, ...]:
        if self._roster_planner is None:
            return ()
        candidates, current, minimum, maximum, required = self._roster_plan_inputs()
        selected = required + self._roster_planner.select_next(
            candidates,
            current_roster=current,
            scene_events=scene_events,
            min_count=minimum,
            max_count=maximum,
        )
        self._replace_roster(selected)
        self._roster_planned = True
        return selected

    def _roster_plan_inputs(
        self,
    ) -> tuple[
        dict[str, tuple[str, str]],
        tuple[str, ...],
        int,
        int,
        tuple[str, ...],
    ]:
        candidates = self._active_roster_candidates()
        current = self.roster_actor_ids()
        if self.player_actor_id is None:
            return candidates, current, 1, MAX_SCENE_ROSTER_SIZE, ()
        if self.player_actor_id not in candidates:
            raise ValueError("interactive player is not an active Actor")
        npc_candidates = {
            actor_id: value
            for actor_id, value in candidates.items()
            if actor_id != self.player_actor_id
        }
        npc_current = tuple(
            actor_id for actor_id in current if actor_id != self.player_actor_id
        )
        return (
            npc_candidates,
            npc_current,
            0,
            MAX_SCENE_ROSTER_SIZE - 1,
            (self.player_actor_id,),
        )

    def _cancelled(self, external: Event) -> bool:
        return self.cancellation.is_set() or external.is_set()

    def _check_cancelled(self, external: Event) -> None:
        if self._cancelled(external):
            self.cancellation.set()
            raise SimulationCancelledError("simulation was cancelled")

    def set_observer(self, observer: SimulationObserver) -> None:
        self._observer = observer

    def _set_trace_context(
        self,
        *,
        step: int,
        component_ids: tuple[str, ...],
        source_record_ids: tuple[str, ...] = (),
        stage: SimulationStage,
        task_label: str,
        stage_event_id: str,
    ) -> None:
        for model in self._language_models:
            setter = getattr(model, "set_trace_context", None)
            if setter is not None:
                setter(
                    step=step,
                    component_ids=component_ids,
                    source_record_ids=source_record_ids,
                    stage=stage.value,
                    task_label=task_label,
                    stage_event_id=stage_event_id,
                )

    def _publish_stage(
        self,
        *,
        step: int,
        stage: SimulationStage,
        status: StageStatus,
        started_at: datetime,
        actor_id: str | None = None,
        action_spec: ActionSpec | None = None,
        summary_text: str | None = None,
        input_record_ids: tuple[str, ...] = (),
        output_record_ids: tuple[str, ...] = (),
        visible_to: tuple[str, ...] = (),
        completed_at: datetime | None = None,
        error_code: str | None = None,
    ) -> SimulationStageEvent:
        finished = completed_at or (
            datetime.now(UTC) if status != StageStatus.RUNNING else None
        )
        duration_ms = (
            max(0, int((finished - started_at).total_seconds() * 1000))
            if finished is not None
            else None
        )
        stage_calls = (
            tuple(
                trace
                for trace in self._model_traces
                if trace.step == step
                and trace.started_at >= started_at
                and (finished is None or trace.started_at <= finished)
            )
            if finished is not None
            else ()
        )
        event = SimulationStageEvent(
            event_id=f"stage-event:{uuid.uuid4().hex}",
            project_id=self.project_id,
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            stage=stage,
            status=status,
            actor_id=actor_id,
            action_spec=action_spec,
            summary_text=summary_text,
            input_record_ids=input_record_ids,
            output_record_ids=output_record_ids,
            visible_to=visible_to,
            profile_ids=tuple(dict.fromkeys(trace.profile_id for trace in stage_calls)),
            model_refs=tuple(
                dict.fromkeys(
                    trace.model_ref
                    for trace in stage_calls
                    if trace.model_ref is not None
                )
            ),
            prompt_tokens=sum(trace.prompt_tokens for trace in stage_calls),
            completion_tokens=sum(trace.completion_tokens for trace in stage_calls),
            duration_ms=duration_ms,
            error_code=error_code,
            started_at=started_at,
            completed_at=finished,
        )
        self._stage_events.append(event)
        if self._observer is not None:
            self._observer.publish(event)
        return event

    def _evaluate_termination(self, *, step: int, started_at: datetime) -> bool:
        stage_event = self._publish_stage(
            step=step,
            stage=SimulationStage.TERMINATION,
            status=StageStatus.RUNNING,
            started_at=started_at,
        )
        self._set_trace_context(
            step=step,
            component_ids=("game-master:termination",),
            stage=SimulationStage.TERMINATION,
            task_label="终止判断",
            stage_event_id=stage_event.event_id,
        )
        should_terminate, _ = self.game_master.should_terminate(
            session_id=self.session_id,
            step=step,
        )
        self._publish_stage(
            step=step,
            stage=SimulationStage.TERMINATION,
            status=StageStatus.SUCCEEDED,
            started_at=started_at,
            summary_text=(
                "Game Master ended the session"
                if should_terminate
                else "Simulation continues"
            ),
        )
        return should_terminate

    def execute_step(
        self,
        step: int,
        *,
        cancellation: Event,
        eligible_actor_ids: tuple[str, ...] | None = None,
        deferred_boundary: SimulationBoundary = SimulationBoundary.NONE,
    ) -> StepResult:
        current_stage = SimulationStage.TERMINATION
        stage_started = datetime.now(UTC)
        try:
            self._check_cancelled(cancellation)
            if eligible_actor_ids is None and self._evaluate_termination(
                step=step,
                started_at=stage_started,
            ):
                return StepResult(
                    session_id=self.session_id,
                    branch_id=self.branch_id,
                    step=step,
                    acting_actor_id=None,
                    action_spec=None,
                    action_text=None,
                    resolved_turn=None,
                    status=TurnSessionStatus.TERMINATED,
                )

            current_stage = SimulationStage.OBSERVATION
            stage_started = datetime.now(UTC)
            stage_event = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
            )
            self._set_trace_context(
                step=step,
                component_ids=("game-master:roster-selection",),
                stage=current_stage,
                task_label="初始角色选择",
                stage_event_id=stage_event.event_id,
            )
            selected_roster = self._plan_initial_roster()
            eligible_actors = self.actors
            if eligible_actor_ids is not None:
                eligible_ids = set(eligible_actor_ids)
                unknown_eligible_ids = eligible_ids - set(self._actors_by_name)
                if unknown_eligible_ids:
                    raise ValueError(
                        "eligible actors are not in the current roster: "
                        f"{sorted(unknown_eligible_ids)}"
                    )
                eligible_actors = tuple(
                    actor for actor in self.actors if actor.name in eligible_ids
                )
                if not eligible_actors:
                    raise ValueError("automatic step requires an eligible actor")
            observation_actors = (
                eligible_actors if eligible_actor_ids is not None else self.actors
            )
            observation_ids: list[str] = []
            observation_summaries: list[str] = []
            for actor in observation_actors:
                self._check_cancelled(cancellation)
                record_id = f"observation:{self.session_id}:{step}:{actor.name}"
                self._set_trace_context(
                    step=step,
                    component_ids=("game-master:observation",),
                    source_record_ids=(record_id,),
                    stage=current_stage,
                    task_label="角色观察",
                    stage_event_id=stage_event.event_id,
                )
                observation = self.game_master.make_observation(
                    actor,
                    session_id=self.session_id,
                    step=step,
                    content_locale=self.content_locale,
                    context_text=self._perception_context_prompt(actor.name),
                ).observation_text
                if observation.strip():
                    observation_ids.append(record_id)
                    observation_summaries.append(f"{actor.name}: {observation}")
                    actor.observe(
                        PerceptionFrame(
                            frame_id=record_id,
                            session_id=self.session_id,
                            branch_id=self.branch_id,
                            actor_id=actor.name,
                            step=step,
                            content_locale=self.content_locale,
                            observation_text=observation,
                            participant_ids=self._memory_participant_ids(actor.name),
                            location_ids=self._memory_location_ids(actor.name),
                        )
                    )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                summary_text=(
                    (
                        f"Initial roster: {', '.join(selected_roster)}\n"
                        if selected_roster
                        else ""
                    )
                    + ("\n".join(observation_summaries) or "No new observations")
                ),
                output_record_ids=tuple(observation_ids),
                visible_to=tuple(actor.name for actor in observation_actors),
            )

            self._check_cancelled(cancellation)
            current_stage = SimulationStage.ACTOR_SELECTION
            stage_started = datetime.now(UTC)
            stage_event = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                input_record_ids=tuple(observation_ids),
            )
            self._set_trace_context(
                step=step,
                component_ids=("game-master:actor-selection",),
                source_record_ids=tuple(observation_ids),
                stage=current_stage,
                task_label="行动角色选择",
                stage_event_id=stage_event.event_id,
            )
            actor_id = self.game_master.select_next_actor(
                eligible_actors,
                session_id=self.session_id,
                step=step,
            )
            actor = self._actors_by_name[actor_id]
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                summary_text=f"{actor.name} selected as the next actor",
                input_record_ids=tuple(observation_ids),
            )

            current_stage = SimulationStage.ACTION_SPEC
            stage_started = datetime.now(UTC)
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
            )
            action_spec = self.game_master.create_action_spec(
                actor,
                session_id=self.session_id,
                step=step,
                content_locale=self.content_locale,
            )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                action_spec=action_spec,
                summary_text=action_spec.call_to_action,
            )
            if action_spec.output_type == ActionOutputType.SKIP_THIS_STEP:
                return StepResult(
                    session_id=self.session_id,
                    branch_id=self.branch_id,
                    step=step,
                    acting_actor_id=actor.name,
                    action_spec=action_spec,
                    action_text=None,
                    resolved_turn=None,
                    status=TurnSessionStatus.RUNNING,
                )

            current_stage = SimulationStage.ACTOR_ACTION
            stage_started = datetime.now(UTC)
            actor_observation_ids = tuple(
                record_id
                for record_id in observation_ids
                if record_id.endswith(f":{actor.name}")
            )
            stage_event = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                action_spec=action_spec,
                input_record_ids=actor_observation_ids,
            )
            self._set_trace_context(
                step=step,
                component_ids=(f"actor:{actor.name}:action",),
                source_record_ids=actor_observation_ids,
                stage=current_stage,
                task_label="角色行动",
                stage_event_id=stage_event.event_id,
            )
            self._sync_actor_states({actor.name})
            action = actor.act(action_spec)
            putative_id = f"putative:{self.session_id}:{step}"
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                action_spec=action_spec,
                summary_text=action,
                input_record_ids=actor_observation_ids,
                output_record_ids=(putative_id,),
                visible_to=(actor.name,),
            )

            self._check_cancelled(cancellation)
            current_stage = SimulationStage.RESOLUTION
            stage_started = datetime.now(UTC)
            stage_event = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                input_record_ids=(putative_id,),
            )
            self._set_trace_context(
                step=step,
                component_ids=("game-master:resolution",),
                source_record_ids=(putative_id,),
                stage=current_stage,
                task_label="世界结算",
                stage_event_id=stage_event.event_id,
            )
            def merge_boundary(value: ResolvedTurn) -> ResolvedTurn:
                merged = value.boundary.merge(deferred_boundary)
                return (
                    value
                    if merged == value.boundary
                    else value.model_copy(update={"boundary": merged})
                )

            resolved, character_effect_ids = self._resolve_and_apply_atomically(
                lambda: self.resolver.resolve(
                    self.game_master,
                    self._resolver_context(
                        step=step,
                        acting_actor_id=actor.name,
                        putative_event_text=action,
                    ),
                    cancellation=self.cancellation,
                ),
                transform=merge_boundary,
            )
            event_id = f"event:{self.session_id}:{step}"
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                summary_text=resolved.raw_resolution_text,
                input_record_ids=(putative_id,),
                output_record_ids=(event_id, *character_effect_ids),
            )

            current_stage = SimulationStage.MEMORY_ROUTING
            stage_started = datetime.now(UTC)
            stage_event = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                input_record_ids=(event_id,),
            )
            self._set_trace_context(
                step=step,
                component_ids=("memory:routing",),
                source_record_ids=(event_id,),
                stage=current_stage,
                task_label="记忆路由",
                stage_event_id=stage_event.event_id,
            )
            observer_ids: set[str] = set() if resolved.events else {actor.name}
            for event in resolved.events:
                observer_ids.update(event.observer_ids)
                if event.visibility == EventVisibility.PUBLIC:
                    observer_ids.update(self._actors_by_name)
                elif event.visibility == EventVisibility.PARTICIPANTS:
                    observer_ids.update(event.participant_ids)
            observer_ids.intersection_update(self._actors_by_name)
            participant_ids = tuple(
                sorted(
                    {
                        participant_id
                        for event in resolved.events
                        for participant_id in event.participant_ids
                    }
                )
            )
            location_ids = tuple(
                sorted(
                    {
                        location_id
                        for event in resolved.events
                        for location_id in event.location_ids
                    }
                )
            )
            routed_ids: list[str] = []
            for observer_id in sorted(observer_ids):
                routed_id = f"event-observation:{self.session_id}:{step}:{observer_id}"
                self._actors_by_name[observer_id].observe(
                    PerceptionFrame(
                        frame_id=routed_id,
                        session_id=self.session_id,
                        branch_id=self.branch_id,
                        actor_id=observer_id,
                        step=step,
                        content_locale=self.content_locale,
                        observation_text=resolved.raw_resolution_text,
                        participant_ids=participant_ids,
                        source_record_ids=(event_id,),
                        location_ids=self._memory_location_ids(
                            observer_id, location_ids
                        ),
                        tags=((action_spec.tag,) if action_spec.tag else ()),
                    )
                )
                routed_ids.append(routed_id)
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                summary_text=(
                    "World result routed to " + ", ".join(sorted(observer_ids))
                ),
                input_record_ids=(event_id,),
                output_record_ids=tuple(routed_ids),
                visible_to=tuple(sorted(observer_ids)),
            )
            if resolved.boundary.value != "none":
                current_stage = SimulationStage.ACTOR_SELECTION
                stage_started = datetime.now(UTC)
            self._advance_scene_boundary(
                resolved,
                event_id=event_id,
                started_at=stage_started,
            )
            step_status = TurnSessionStatus.RUNNING
            if eligible_actor_ids is not None:
                self._check_cancelled(cancellation)
                current_stage = SimulationStage.TERMINATION
                stage_started = datetime.now(UTC)
                if self._evaluate_termination(
                    step=step,
                    started_at=stage_started,
                ):
                    step_status = TurnSessionStatus.TERMINATED
            return StepResult(
                session_id=self.session_id,
                branch_id=self.branch_id,
                step=step,
                acting_actor_id=actor.name,
                action_spec=action_spec,
                action_text=action,
                resolved_turn=resolved,
                status=step_status,
                boundary=resolved.boundary,
            )
        except Exception as error:
            status = (
                StageStatus.CANCELLED
                if isinstance(error, SimulationCancelledError)
                else StageStatus.FAILED
            )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=status,
                started_at=stage_started,
                summary_text=str(error),
                error_code=getattr(error, "code", type(error).__name__.lower()),
            )
            raise

    def _human_belief_effect(
        self,
        text: str,
        *,
        step: int,
    ) -> StateEffect | None:
        """Build a deterministic audited effect for an explicit player belief."""
        if self.player_actor_id is None:
            return None
        player = self._characters_by_id.get(self.player_actor_id)
        if player is None:
            return None
        match = re.match(r"^\s*(?:我确定|我相信|我认为)\s*(.+?)\s*[。!]?\s*$", text)
        if match is None:
            return None
        belief = match.group(1).strip()
        if not belief or belief in player.beliefs:
            return None
        return StateEffect(
            effect_id=f"belief-effect:{self.session_id}:{step}:{player.id}",
            operation=EffectOperation.SET,
            target=EffectTarget.CHARACTER_PROJECTION,
            target_id=player.id,
            path="beliefs",
            before=list(player.beliefs),
            after=[*player.beliefs, belief],
            reason_text=f"Player asserted belief: {belief}",
            required=True,
            source_record_ids=(f"putative:{self.session_id}:{step}",),
        )

    def execute_human_turn(
        self,
        step: int,
        *,
        text: str,
        cancellation: Event,
    ) -> StepResult:
        """Resolve one player intent through the same Concordia GM commit path."""
        if self.player_actor_id is None:
            raise ValueError("interactive turn requires a player actor")
        actor = self._actors_by_name.get(self.player_actor_id)
        if actor is None:
            raise ValueError("player actor must be part of the current scene roster")
        action = text.strip()
        if not action:
            raise ValueError("interactive turn text must not be empty")
        self.game_master.set_active_actor(actor.display_name)

        current_stage = SimulationStage.ACTOR_ACTION
        stage_started = datetime.now(UTC)
        try:
            self._check_cancelled(cancellation)
            putative_id = f"putative:{self.session_id}:{step}"
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
            )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                summary_text=action,
                output_record_ids=(putative_id,),
                visible_to=(actor.name,),
            )

            self._check_cancelled(cancellation)
            current_stage = SimulationStage.RESOLUTION
            stage_started = datetime.now(UTC)
            resolution_stage = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                input_record_ids=(putative_id,),
            )
            self._set_trace_context(
                step=step,
                component_ids=("game-master:resolution",),
                source_record_ids=(putative_id,),
                stage=current_stage,
                task_label="玩家意图结算",
                stage_event_id=resolution_stage.event_id,
            )
            belief_effect = self._human_belief_effect(action, step=step)
            resolved, character_effect_ids = self._resolve_and_apply_atomically(
                lambda: self.resolver.resolve(
                    self.game_master,
                    self._resolver_context(
                        step=step,
                        acting_actor_id=actor.name,
                        putative_event_text=action,
                    ),
                    cancellation=self.cancellation,
                ),
                transform=(
                    None
                    if belief_effect is None
                    else lambda value: value.model_copy(
                        update={"effects": (*value.effects, belief_effect)}
                    )
                ),
            )
            follow_up_actor_ids = self._response_npc_actor_ids(resolved)
            event_id = f"event:{self.session_id}:{step}"
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                summary_text=resolved.raw_resolution_text,
                input_record_ids=(putative_id,),
                output_record_ids=(event_id, *character_effect_ids),
            )

            current_stage = SimulationStage.MEMORY_ROUTING
            stage_started = datetime.now(UTC)
            routing_stage = self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.RUNNING,
                started_at=stage_started,
                actor_id=actor.name,
                input_record_ids=(event_id,),
            )
            self._set_trace_context(
                step=step,
                component_ids=("memory:routing",),
                source_record_ids=(event_id,),
                stage=current_stage,
                task_label="玩家事件记忆路由",
                stage_event_id=routing_stage.event_id,
            )
            observer_ids: set[str] = set() if resolved.events else {actor.name}
            for event in resolved.events:
                observer_ids.update(event.observer_ids)
                if event.visibility == EventVisibility.PUBLIC:
                    observer_ids.update(self._actors_by_name)
                elif event.visibility == EventVisibility.PARTICIPANTS:
                    observer_ids.update(event.participant_ids)
            observer_ids.intersection_update(self._actors_by_name)
            participant_ids = tuple(
                sorted(
                    {
                        participant_id
                        for event in resolved.events
                        for participant_id in event.participant_ids
                    }
                )
            )
            location_ids = tuple(
                sorted(
                    {
                        location_id
                        for event in resolved.events
                        for location_id in event.location_ids
                    }
                )
            )
            routed_ids: list[str] = []
            for observer_id in sorted(observer_ids):
                routed_id = f"event-observation:{self.session_id}:{step}:{observer_id}"
                self._actors_by_name[observer_id].observe(
                    PerceptionFrame(
                        frame_id=routed_id,
                        session_id=self.session_id,
                        branch_id=self.branch_id,
                        actor_id=observer_id,
                        step=step,
                        content_locale=self.content_locale,
                        observation_text=resolved.raw_resolution_text,
                        participant_ids=participant_ids,
                        source_record_ids=(event_id,),
                        location_ids=self._memory_location_ids(
                            observer_id, location_ids
                        ),
                    )
                )
                routed_ids.append(routed_id)
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=StageStatus.SUCCEEDED,
                started_at=stage_started,
                actor_id=actor.name,
                summary_text=(
                    "World result routed to " + ", ".join(sorted(observer_ids))
                ),
                input_record_ids=(event_id,),
                output_record_ids=tuple(routed_ids),
                visible_to=tuple(sorted(observer_ids)),
            )
            if resolved.boundary != SimulationBoundary.NONE and not follow_up_actor_ids:
                current_stage = SimulationStage.ACTOR_SELECTION
                stage_started = datetime.now(UTC)
            self._advance_scene_boundary(
                resolved,
                event_id=event_id,
                started_at=stage_started,
                finalize=not follow_up_actor_ids,
            )
            return StepResult(
                session_id=self.session_id,
                branch_id=self.branch_id,
                step=step,
                acting_actor_id=actor.name,
                action_spec=None,
                action_text=action,
                resolved_turn=resolved,
                status=TurnSessionStatus.RUNNING,
                boundary=(
                    SimulationBoundary.NONE
                    if follow_up_actor_ids
                    else resolved.boundary
                ),
                follow_up_actor_ids=follow_up_actor_ids,
            )
        except Exception as error:
            status = (
                StageStatus.CANCELLED
                if isinstance(error, SimulationCancelledError)
                else StageStatus.FAILED
            )
            self._publish_stage(
                step=step,
                stage=current_stage,
                status=status,
                started_at=stage_started,
                summary_text=str(error),
                error_code=getattr(error, "code", type(error).__name__.lower()),
            )
            raise

    def execute_world_initiative(
        self,
        step: int,
        *,
        trigger: InitiativeTrigger,
        cancellation: Event,
    ) -> StepResult:
        """Ask the existing GM for one world-owned external change."""
        self._check_cancelled(cancellation)
        started_at = datetime.now(UTC)
        stage_event = self._publish_stage(
            step=step,
            stage=SimulationStage.RESOLUTION,
            status=StageStatus.RUNNING,
            started_at=started_at,
            summary_text=f"World initiative: {trigger.reason}",
        )
        self._set_trace_context(
            step=step,
            component_ids=("game-master:world-initiative",),
            stage=SimulationStage.RESOLUTION,
            task_label="世界主动事件",
            stage_event_id=stage_event.event_id,
        )
        set_active = getattr(self.game_master, "set_active_actor", None)
        if set_active is not None:
            set_active(self.actors[0].display_name)
        resolved, changed_ids = self._resolve_and_apply_atomically(
            lambda: self.resolver.resolve_initiative(
                self.game_master,
                self._initiative_context(step=step, trigger=trigger),
                cancellation=self.cancellation,
            )
        )
        event_id = f"event:{self.session_id}:{step}"
        self._publish_stage(
            step=step,
            stage=SimulationStage.RESOLUTION,
            status=StageStatus.SUCCEEDED,
            started_at=started_at,
            summary_text=resolved.raw_resolution_text,
            output_record_ids=(event_id, *changed_ids),
        )
        observer_ids: set[str] = set()
        participant_ids: set[str] = set()
        for event in resolved.events:
            observer_ids.update(event.observer_ids)
            participant_ids.update(event.participant_ids)
            if event.visibility == EventVisibility.PUBLIC:
                observer_ids.update(self._actors_by_name)
            elif event.visibility == EventVisibility.PARTICIPANTS:
                observer_ids.update(event.participant_ids)
        observer_ids.intersection_update(self._actors_by_name)
        for observer_id in sorted(observer_ids):
            self._actors_by_name[observer_id].observe(
                PerceptionFrame(
                    frame_id=(
                        f"event-observation:{self.session_id}:{step}:{observer_id}"
                    ),
                    session_id=self.session_id,
                    branch_id=self.branch_id,
                    actor_id=observer_id,
                    step=step,
                    content_locale=self.content_locale,
                    observation_text=resolved.raw_resolution_text,
                    participant_ids=tuple(sorted(participant_ids)),
                    source_record_ids=(event_id,),
                    location_ids=self._memory_location_ids(observer_id),
                )
            )
        self._advance_scene_boundary(
            resolved,
            event_id=event_id,
            started_at=started_at,
        )
        if trigger.reason == "due_clock" and trigger.source_id is not None:
            self._handled_clock_ids.add(trigger.source_id)
        return StepResult(
            session_id=self.session_id,
            branch_id=self.branch_id,
            step=step,
            acting_actor_id=None,
            action_spec=None,
            action_text=None,
            resolved_turn=resolved,
            status=TurnSessionStatus.RUNNING,
            boundary=resolved.boundary,
        )

    def actor_states(self) -> dict[str, dict[str, JsonValue]]:
        return {
            actor.name: actor.get_state() for actor in self._all_actors_by_name.values()
        }

    def game_master_states(self) -> dict[str, dict[str, JsonValue]]:
        return {self.game_master.name: self.game_master.get_state()}

    def _memory_banks(self) -> dict[str, ConcordiaMemoryBank]:
        memories: dict[str, ConcordiaMemoryBank] = {}
        for actor in self._all_actors_by_name.values():
            memory = getattr(actor, "memory", None)
            if isinstance(memory, ConcordiaMemoryBank):
                memories[memory.owner_id] = memory
        game_master_memory = getattr(self.game_master, "memory", None)
        if isinstance(game_master_memory, ConcordiaMemoryBank):
            memories[game_master_memory.owner_id] = game_master_memory
        return memories

    def pending_memory_records(self) -> tuple[MemoryRecord, ...]:
        return tuple(
            record
            for bank in self._memory_banks().values()
            for record in bank.pending_records()
        )

    def mark_memory_committed(self) -> None:
        for bank in self._memory_banks().values():
            bank.mark_committed()

    def prepare_step_memory(self, *, checkpoint_id: str, step: int) -> None:
        """Materialize newly applicable instructions before the next turn."""

        if self._project_root is None:
            return
        from story_engine.persistence.checkpoint_store import CheckpointStore
        from story_engine.wiki.store import WikiStore

        reachable_checkpoints = set(
            CheckpointStore(self._project_root).lineage(checkpoint_id)
        )
        existing_ids = {
            record.record_id for record in self.game_master.memory.records()
        }
        for instruction in WikiStore(
            self._project_root, self.branch_id
        ).list_instructions():
            if (
                instruction.instruction_id in existing_ids
                or instruction.applies_from_checkpoint_id not in reachable_checkpoints
            ):
                continue
            self.game_master.memory.add(
                MemoryRecord(
                    record_id=instruction.instruction_id,
                    record_type=MemoryRecordType.SYSTEM,
                    scope=self.game_master.memory.scope,
                    owner_id=self.game_master.name,
                    session_id=self.session_id,
                    branch_id=self.branch_id,
                    step=step,
                    text=instruction.text,
                    content_locale=self.content_locale,
                    created_at=instruction.created_at,
                    source_record_ids=(instruction.applies_from_checkpoint_id,),
                    tags=("director_instruction",),
                    importance=1,
                )
            )
            existing_ids.add(instruction.instruction_id)

    def replay_memory_records(self, records: Sequence[MemoryRecord]) -> None:
        banks = self._memory_banks()
        for bank in banks.values():
            bank.replace(())
        for record in records:
            target_bank = banks.get(record.owner_id)
            if target_bank is None:
                raise ValueError(
                    f"memory history owner {record.owner_id!r} is unavailable"
                )
            target_bank.add(record)
        canonical_facts: list[Fact] = []
        for record in records:
            if (
                record.owner_id != self.game_master.name
                or "canonical_fact" not in record.tags
            ):
                continue
            visibility = next(
                (tag for tag in record.tags if tag in {"public", "private", "secret"}),
                None,
            )
            prefix = "seed:"
            suffix = ":gm"
            if (
                visibility is None
                or not record.record_id.startswith(prefix)
                or not record.record_id.endswith(suffix)
                or not record.source_record_ids
            ):
                raise ValueError("canonical fact memory history is invalid")
            canonical_facts.append(
                Fact(
                    id=record.record_id[len(prefix) : -len(suffix)],
                    statement=record.text,
                    visibility=cast(FactVisibility, visibility),
                    known_by=record.actor_ids,
                    source_event_id=record.source_record_ids[0],
                    introduced_at=record.created_at,
                )
            )
        self._canonical_facts = tuple(canonical_facts)
        self.mark_memory_committed()

    def drain_model_traces(self) -> tuple[ModelCallTrace, ...]:
        traces = tuple(self._model_traces)
        self._model_traces.clear()
        return traces

    def drain_stage_events(self) -> tuple[SimulationStageEvent, ...]:
        events = tuple(self._stage_events)
        self._stage_events.clear()
        return events

    def set_content_locale(self, content_locale: str) -> None:
        for actor in self._all_actors_by_name.values():
            actor.set_content_locale(content_locale)
        self.game_master.set_content_locale(content_locale)
        for model in self._language_models:
            setter = getattr(model, "set_content_locale", None)
            if setter is not None:
                setter(content_locale)
        self.content_locale = content_locale

    def restore_states(
        self,
        *,
        actor_states: Mapping[str, Mapping[str, JsonValue]],
        game_master_states: Mapping[str, Mapping[str, JsonValue]],
    ) -> None:
        for actor in self._all_actors_by_name.values():
            actor.set_state(dict(actor_states[actor.name]))
        self.game_master.set_state(dict(game_master_states[self.game_master.name]))
        self._sync_actor_states(set(self._all_actors_by_name))

    def restore_snapshot(self, snapshot: TurnSessionSnapshot) -> None:
        self._characters_by_id = {
            character.id: character for character in snapshot.characters
        }
        self.restore_states(
            actor_states=snapshot.actor_states,
            game_master_states=snapshot.game_master_states,
        )
        if snapshot.checkpoint_id is not None and self._project_root is not None:
            from story_engine.persistence.checkpoint_store import CheckpointStore
            from story_engine.persistence.simulation_log import SimulationLogStore

            records = SimulationLogStore(self._project_root).reachable_memory_records(
                CheckpointStore(self._project_root),
                snapshot.checkpoint_id,
                branch_id=self.branch_id,
            )
            self.replay_memory_records(records)
        self.set_content_locale(snapshot.content_locale)
        self._world = snapshot.world
        self.player_actor_id = snapshot.player_actor_id
        self._pending_scene_events = list(snapshot.pending_scene_events)
        self._turns_without_material_world_change = (
            snapshot.turns_without_material_world_change
        )
        self._turns_since_last_initiative = snapshot.turns_since_last_initiative
        self._handled_clock_ids = set(snapshot.handled_clock_ids)
