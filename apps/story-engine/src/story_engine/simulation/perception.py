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
        events = (
            result.resolved_turn.events if result.resolved_turn is not None else ()
        )
        visible_events = tuple(
            event.event_text for event in events if _is_visible(event, player_actor_id)
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
        perception = PlayerPerception(
            scene_text=world.scene_text,
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

    def initial(self, snapshot: TurnSessionSnapshot) -> InteractiveTurnResponse:
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
        )
