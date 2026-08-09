"""Player-facing projections derived from one durable world snapshot."""

from story_engine.domain.base import Identifier, RuntimeModel
from story_engine.domain.projection import EventVisibility, ResolvedEvent
from story_engine.domain.simulation import StepResult, TurnSessionSnapshot


class PlayerState(RuntimeModel):
    identity: str
    capabilities: tuple[str, ...] = ()
    conditions: tuple[str, ...] = ()
    possessions: tuple[str, ...] = ()
    relationships: tuple[str, ...] = ()


class PlayerPerception(RuntimeModel):
    scene_text: str
    player_state_summary: str
    visible_changes: tuple[str, ...] = ()
    checkpoint_id: Identifier
    world_time: str


class InteractiveTurnResponse(RuntimeModel):
    perception: PlayerPerception
    visible_events: tuple[str, ...] = ()
    player_state: PlayerState
    checkpoint_id: Identifier
    world_time: str


class CheckpointTimelineEntry(RuntimeModel):
    checkpoint_id: Identifier
    step: int
    world_time: str
    is_current: bool


def _is_visible(event: ResolvedEvent, player_actor_id: str) -> bool:
    if event.visibility == EventVisibility.PUBLIC:
        return True
    if event.visibility == EventVisibility.PARTICIPANTS:
        return player_actor_id in event.participant_ids
    if event.visibility == EventVisibility.RESTRICTED:
        return player_actor_id in event.observer_ids
    return False


class PerceptionBuilder:
    """Expose only world details and committed events visible to the player."""

    def build(
        self,
        snapshot: TurnSessionSnapshot,
        result: StepResult,
        *,
        scene_events: tuple[ResolvedEvent, ...] | None = None,
    ) -> InteractiveTurnResponse:
        player_actor_id = snapshot.player_actor_id
        world = snapshot.world
        if player_actor_id is None or world is None or snapshot.checkpoint_id is None:
            raise ValueError("interactive session has no durable player world state")
        player = next(
            (
                character
                for character in snapshot.characters
                if character.id == player_actor_id
            ),
            None,
        )
        if player is None:
            raise ValueError(
                "interactive session player is missing from its character state"
            )
        result_events = (
            result.resolved_turn.events if result.resolved_turn is not None else ()
        )
        visible_events = tuple(
            event.event_text
            for event in result_events
            if _is_visible(event, player_actor_id)
        )
        current_scene_events = (
            scene_events
            if scene_events is not None
            else snapshot.pending_scene_events or result_events
        )
        visible_scene_events = tuple(
            event.event_text
            for event in current_scene_events
            if _is_visible(event, player_actor_id)
        )
        relationships = tuple(
            relationship.description for relationship in player.relationships
        )
        player_state = PlayerState(
            identity=player.identity,
            capabilities=player.capabilities,
            conditions=player.conditions,
            possessions=player.resources,
            relationships=relationships,
        )
        summary_parts = [player.identity]
        if player.conditions:
            summary_parts.append("; ".join(player.conditions))
        if player.resources:
            summary_parts.append("; ".join(player.resources))
        current_location = player.location or world.current_location or "未知地点"
        if visible_scene_events:
            scene_text = "\n\n".join(
                (f"{world.current_time} · {current_location}", *visible_scene_events)
            )
        elif snapshot.current_step == 0:
            scene_text = world.scene_text
        else:
            scene_text = (
                f"{world.current_time} · {current_location}\n\n"
                "你没有观察到新的可见变化。"
            )
        perception = PlayerPerception(
            scene_text=scene_text,
            player_state_summary="\n".join(summary_parts),
            visible_changes=visible_events,
            checkpoint_id=snapshot.checkpoint_id,
            world_time=world.current_time,
        )
        return InteractiveTurnResponse(
            perception=perception,
            visible_events=visible_events,
            player_state=player_state,
            checkpoint_id=snapshot.checkpoint_id,
            world_time=world.current_time,
        )

    def initial(
        self,
        snapshot: TurnSessionSnapshot,
        *,
        scene_events: tuple[ResolvedEvent, ...] | None = None,
    ) -> InteractiveTurnResponse:
        return self.build(
            snapshot,
            StepResult(
                session_id=snapshot.session_id,
                branch_id=snapshot.branch_id,
                step=snapshot.current_step,
                acting_actor_id=snapshot.player_actor_id,
                action_spec=None,
                action_text=None,
                resolved_turn=None,
                status=snapshot.status,
            ),
            scene_events=scene_events,
        )
