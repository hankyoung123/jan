from datetime import UTC, datetime

from story_engine.domain.models import Character, WorldState
from story_engine.domain.projection import EventVisibility, ResolvedEvent, ResolvedTurn
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    StepResult,
    TurnSessionRequest,
    TurnSessionSnapshot,
    TurnSessionStatus,
)
from story_engine.simulation.perception import PerceptionBuilder
from story_engine.submission.service import last_ferry_before_submission


def _snapshot() -> TurnSessionSnapshot:
    request = TurnSessionRequest(
        project_id="last-ferry-before",
        branch_id="main",
        premise_text="港口旅馆",
        actor_ids=("player", "lin-che"),
        player_actor_id="player",
        content_locale="zh-CN",
        control=ControlPolicy(mode=ControlMode.STEP),
    )
    return TurnSessionSnapshot(
        session_id="session:world",
        project_id=request.project_id,
        branch_id="main",
        status=TurnSessionStatus.PAUSED,
        content_locale="zh-CN",
        request=request,
        player_actor_id="player",
        world=WorldState(
            current_time="18:43",
            current_location="港口旅馆",
            scene_text="雨水沿着门口的地毯渗开。",
        ),
        characters=(
            Character(
                id="player",
                display_name="你",
                type="active",
                identity="本地调查记者",
                core_desire="查明真相",
                current_goal="调查",
                capabilities=("摄影",),
                conditions=("右手轻伤",),
                resources=("相机",),
            ),
            Character(
                id="lin-che",
                display_name="林澈",
                type="active",
                identity="旧友",
                core_desire="保守秘密",
                current_goal="离开",
            ),
        ),
        current_step=1,
        actor_states={},
        game_master_states={},
        memory_snapshots={},
        raw_log_offset=1,
        checkpoint_id="checkpoint:world",
        started_at=datetime(2026, 8, 8, tzinfo=UTC),
        updated_at=datetime(2026, 8, 8, tzinfo=UTC),
        state_hash="0" * 64,
    )


def _event(
    event_id: str,
    text: str,
    visibility: EventVisibility,
    **kwargs,
) -> ResolvedEvent:
    return ResolvedEvent(
        event_id=event_id,
        session_id="session:world",
        step=1,
        actor_id="lin-che",
        event_text=text,
        visibility=visibility,
        content_locale="zh-CN",
        occurred_at=datetime(2026, 8, 8, tzinfo=UTC),
        **kwargs,
    )


def test_case_08_perception_excludes_private_events_and_internal_state() -> None:
    snapshot = _snapshot()
    result = StepResult(
        session_id=snapshot.session_id,
        branch_id="main",
        step=1,
        acting_actor_id="player",
        action_spec=None,
        action_text="看看大厅",
        resolved_turn=ResolvedTurn(
            session_id=snapshot.session_id,
            branch_id="main",
            step=1,
            raw_resolution_text="内部结算文本",
            events=(
                _event(
                    "event:public",
                    "张野看了一眼门口。",
                    EventVisibility.PUBLIC,
                ),
                _event(
                    "event:private",
                    "林澈记得张野篡改了记录。",
                    EventVisibility.RESTRICTED,
                    observer_ids=("lin-che",),
                ),
                _event(
                    "event:gm",
                    "GM 的秘密推理。",
                    EventVisibility.GM_ONLY,
                ),
            ),
            content_locale="zh-CN",
        ),
        status=TurnSessionStatus.PAUSED,
    )

    response = PerceptionBuilder().build(snapshot, result)

    assert response.visible_events == ("张野看了一眼门口。",)
    assert "篡改" not in response.perception.scene_text
    assert "秘密" not in str(response.model_dump())
    assert response.player_state.possessions == ("相机",)


def test_default_world_has_frozen_truth_seed_and_human_actor() -> None:
    world = last_ferry_before_submission()
    facts = {fact.id: fact for fact in world.facts}
    player = next(
        character for character in world.characters if character.id == "player"
    )

    assert world.id == "last-ferry-before"
    assert len(world.characters) == 4
    assert player.capabilities == ("调查采访", "摄影", "熟悉本地港口", "普通驾驶能力")
    assert player.conditions == ("右手轻伤",)
    assert "枪" not in player.resources
    assert all(
        relationship.character_id != "chen-kai"
        for relationship in player.relationships
    )
    assert "fact:player-knows-chen-kai" in player.known_fact_ids
    assert facts["fact:player-knows-chen-kai"].statement == (
        "你认识当地警员陈凯，可以尝试联系他。"  # noqa: RUF001
    )
    assert all(
        f"truth:{name}" in facts
        for name in (
            "message-sender",
            "why-player-was-called",
            "lin-concealment",
            "zhang-goal",
            "locked-room-use",
            "room-entry",
            "key-item-location",
            "event-timeline",
            "ferry-connection",
            "final",
        )
    )
