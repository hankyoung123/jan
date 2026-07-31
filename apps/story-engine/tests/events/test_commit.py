from pathlib import Path

import pytest

from story_engine.domain.errors import InvalidTransitionError
from story_engine.domain.models import (
    Character,
    CharacterIntent,
    NpcCandidate,
    PromotionCandidate,
    ReviewResult,
    StateChange,
    TurnCandidate,
    WorldOutcome,
    WorldState,
)
from story_engine.events.commit import (
    EventCommitService,
    StateChangeConflictError,
    VersionConflictError,
)
from story_engine.evolution.context import CharacterContextAssembler
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectSeed, ProjectStore


def _seed() -> ProjectSeed:
    return ProjectSeed(
        id="fog-harbor",
        title="雾港",
        genre="悬疑",
        theme="真相与亲情之间的选择",
        tone="克制",
        world=WorldState(
            current_time="暴风雨前夜",
            current_location="雾港",
            public_fact_ids=("fact:lighthouse-controls-night-navigation",),
            version=0,
        ),
        characters=(
            Character(
                id="chen-mo",
                type="active",
                identity="机械工程师",
                core_desire="查明真相",
                current_goal="检查灯塔",
                location="港务所",
                version=0,
            ),
            Character(
                id="lin-lan",
                type="active",
                identity="港务所值班员",
                core_desire="保护进港船只",
                current_goal="维持近港秩序",
                location="港务所",
                version=0,
            ),
            Character(
                id="temporary-pilot",
                display_name="临时引航员",
                type="npc",
                identity="暴风雨中赶到港口的引航员",
                core_desire="让客船安全避开暗礁",
                current_goal="观察近港水流",
                known_fact_ids=("fact:near-harbor-reefs",),
                location="近港码头",
                version=2,
            ),
        ),
    )


def _candidate(*, world_version: int = 0) -> TurnCandidate:
    return TurnCandidate(
        id="turn-000001",
        project_id="fog-harbor",
        base_world_version=world_version,
        base_character_versions={"chen-mo": 0},
        intents=(
            CharacterIntent(
                character_id="chen-mo",
                action="前往灯塔检查灯芯槽",
                target="灯塔",
                goal="查明熄灭原因",
                knowledge_basis=("fact:lighthouse-never-off",),
            ),
        ),
        outcome=WorldOutcome(
            summary="陈默抵达灯塔并发现灯芯槽上的新鲜刮痕。",
            public_results=("陈默进入灯塔",),
            character_changes=(
                StateChange(
                    target_type="character",
                    target_id="chen-mo",
                    field="location",
                    old_value="港务所",
                    new_value="灯塔一层",
                    reason="角色行动产生的位置变化",
                ),
            ),
            world_changes=(
                StateChange(
                    target_type="world",
                    target_id="world",
                    field="current_time",
                    old_value="暴风雨前夜",
                    new_value="暴风雨前夜稍晚",
                    reason="行动消耗了时间",
                ),
            ),
        ),
    )


def _approved_candidate(*, world_version: int = 0) -> TurnCandidate:
    return (
        _candidate(world_version=world_version)
        .with_review(
            ReviewResult(
                mode="turn_review",
                passed=True,
                summary="知识边界和世界规则检查通过。",
            )
        )
        .approve()
    )


def _promotion_candidate(*, version: int = 2) -> PromotionCandidate:
    return PromotionCandidate(
        id=f"promotion-temporary-pilot-v{version}",
        project_id="fog-harbor",
        character_id="temporary-pilot",
        base_character_version=version,
        proposed_goal="主动引导客船避开近港暗礁",
        review=ReviewResult(
            mode="promotion_review",
            passed=True,
            summary="该人物已经形成独立目标并可能主动影响后续局势。",
        ),
    )


def _formal_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*.md"))
        if ".story-engine" not in path.parts
    }


def test_unapproved_candidate_cannot_change_formal_state(tmp_path: Path) -> None:
    root = tmp_path / "fog-harbor"
    ProjectStore(root).create(_seed())
    before = _formal_bytes(root)

    with pytest.raises(InvalidTransitionError, match="approved"):
        EventCommitService(root).commit(_candidate())

    assert _formal_bytes(root) == before


def test_stale_world_version_rejects_commit_without_writes(tmp_path: Path) -> None:
    root = tmp_path / "fog-harbor"
    ProjectStore(root).create(_seed())
    before = _formal_bytes(root)

    with pytest.raises(VersionConflictError, match="world version"):
        EventCommitService(root).commit(_approved_candidate(world_version=9))

    assert _formal_bytes(root) == before


def test_stale_character_version_rejects_commit_without_writes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fog-harbor"
    ProjectStore(root).create(_seed())
    before = _formal_bytes(root)
    candidate = _approved_candidate().model_copy(
        update={"base_character_versions": {"chen-mo": 7}}
    )

    with pytest.raises(VersionConflictError, match="character version"):
        EventCommitService(root).commit(candidate)

    assert _formal_bytes(root) == before


def test_changed_nonparticipant_requires_base_version_without_writes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fog-harbor"
    ProjectStore(root).create(_seed())
    before = _formal_bytes(root)
    outcome = _candidate().outcome.model_copy(
        update={
            "character_changes": (
                StateChange(
                    target_type="character",
                    target_id="lin-lan",
                    field="location",
                    old_value="港务所",
                    new_value="近港码头",
                    reason="林岚前往码头协调客船",
                ),
            )
        }
    )
    candidate = _approved_candidate().with_outcome(outcome)
    candidate = candidate.with_review(
        ReviewResult(
            mode="turn_review",
            passed=True,
            summary="伪造审核不能绕过角色版本检查。",
        )
    ).approve()

    with pytest.raises(
        VersionConflictError,
        match="missing base character version: lin-lan",
    ):
        EventCommitService(root).commit(candidate)

    assert _formal_bytes(root) == before


def test_successful_commit_updates_all_formal_state_consistently(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fog-harbor"
    store = ProjectStore(root)
    store.create(_seed())

    result = EventCommitService(root).commit(_approved_candidate())
    snapshot = store.load()
    events = EventStore(root).list_events()

    assert result.event.id == "event-000001"
    assert result.candidate.status == "committed"
    assert snapshot.world.current_time == "暴风雨前夜稍晚"
    assert snapshot.world.version == 1
    assert snapshot.characters[0].location == "灯塔一层"
    assert snapshot.characters[0].version == 1
    assert snapshot.characters[0].last_event_id == "event-000001"
    assert events == (result.event,)
    assert not any((root / ".story-engine/recovery").iterdir())


def test_participant_version_advances_without_explicit_character_change(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fog-harbor"
    store = ProjectStore(root)
    store.create(_seed())
    candidate = _approved_candidate().with_outcome(
        WorldOutcome(summary="陈默留在原地观察灯塔。")
    )
    candidate = candidate.with_review(
        ReviewResult(
            mode="turn_review",
            passed=True,
            summary="检查通过。",
        )
    ).approve()

    result = EventCommitService(root).commit(candidate)
    character = store.load().characters[0]

    assert character.version == 1
    assert character.last_event_id == result.event.id


def test_npc_id_collision_rejects_commit_without_formal_writes(tmp_path: Path) -> None:
    root = tmp_path / "fog-harbor"
    ProjectStore(root).create(_seed())
    before = _formal_bytes(root)
    candidate = _approved_candidate().with_outcome(
        _approved_candidate().outcome.model_copy(
            update={
                "new_npcs": (
                    NpcCandidate(
                        id="chen-mo",
                        identity="冒用现有角色 ID 的陌生人",
                        purpose="制造冲突",
                    ),
                )
            }
        )
    )
    candidate = candidate.with_review(
        ReviewResult(
            mode="turn_review",
            passed=True,
            summary="伪造的通过结果不能绕过提交边界。",
        )
    ).approve()

    with pytest.raises(StateChangeConflictError, match="NPC ID already exists"):
        EventCommitService(root).commit(candidate)

    assert _formal_bytes(root) == before


def test_user_confirmed_promotion_moves_npc_and_appends_event_atomically(
    tmp_path: Path,
) -> None:
    root = tmp_path / "fog-harbor"
    store = ProjectStore(root)
    store.create(_seed())

    result = EventCommitService(root).promote_npc(_promotion_candidate())
    snapshot = store.load()
    promoted = next(
        character
        for character in snapshot.characters
        if character.id == "temporary-pilot"
    )

    assert result.candidate.status == "committed"
    assert result.event.source_turn_id == "promotion-temporary-pilot-v2"
    assert result.event.approved_by_user is True
    assert promoted.type == "active"
    assert promoted.current_goal == "主动引导客船避开近港暗礁"
    assert promoted.version == 3
    assert promoted.last_event_id == result.event.id
    assert not (root / "characters/npc/temporary-pilot.md").exists()
    assert (root / "characters/active/temporary-pilot.md").exists()
    assert "temporary-pilot" in CharacterContextAssembler().assemble(snapshot)
    assert EventStore(root).list_events() == (result.event,)


def test_stale_promotion_rejects_without_formal_writes(tmp_path: Path) -> None:
    root = tmp_path / "fog-harbor"
    ProjectStore(root).create(_seed())
    before = _formal_bytes(root)

    with pytest.raises(VersionConflictError, match="version changed"):
        EventCommitService(root).promote_npc(_promotion_candidate(version=1))

    assert _formal_bytes(root) == before
    assert EventStore(root).list_events() == ()


def test_batch_failure_rolls_back_every_formal_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "fog-harbor"
    ProjectStore(root).create(_seed())
    before = _formal_bytes(root)

    from story_engine.workspace import transaction

    real_replace = transaction._replace
    calls = 0

    def fail_once(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated filesystem failure")
        real_replace(source, destination)

    monkeypatch.setattr(transaction, "_replace", fail_once)

    with pytest.raises(OSError, match="simulated filesystem failure"):
        EventCommitService(root).commit(_approved_candidate())

    assert _formal_bytes(root) == before
    assert EventStore(root).list_events() == ()
