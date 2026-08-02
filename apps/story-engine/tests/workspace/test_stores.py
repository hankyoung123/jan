import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from story_engine.domain.models import (
    Character,
    CharacterIntent,
    Fact,
    StoryEvent,
    TurnCandidate,
    WorldOutcome,
    WorldState,
)
from story_engine.workspace.candidate_store import CandidateStore
from story_engine.workspace.event_store import EventStore
from story_engine.workspace.project_store import ProjectSeed, ProjectStore


def _seed() -> ProjectSeed:
    return ProjectSeed(
        id="fog-harbor",
        title="雾港",
        genre="悬疑",
        theme="真相与亲情之间的选择",
        tone="克制、现实、缓慢积压",
        world=WorldState(
            current_time="暴风雨前夜",
            current_location="雾港",
            active_pressures=("一艘客船即将进港",),
            public_fact_ids=("fact:lighthouse-never-off-at-night",),
            world_variables={"lighthouse_lit": False},
            version=0,
        ),
        characters=(
            Character(
                id="chen-mo",
                type="active",
                identity="从外地返回雾港的机械工程师",
                core_desire="查明父亲失踪的真相",
                current_goal="找到灯塔熄灭的原因",
                known_fact_ids=("fact:father-disappeared-near-lighthouse",),
                version=0,
            ),
            Character(
                id="lin-lan",
                type="active",
                identity="港务值班员",
                core_desire="保护妹妹",
                current_goal="让客船安全进港",
                known_fact_ids=("fact:storm-approaching",),
                version=0,
            ),
        ),
        facts=(
            Fact(
                id="fact:lighthouse-never-off-at-night",
                statement="灯塔夜间从不熄灭。",
                visibility="public",
                source_event_id="submission:fog-harbor",
                introduced_at=datetime(2026, 7, 31, tzinfo=UTC),
            ),
            Fact(
                id="fact:father-disappeared-near-lighthouse",
                statement="陈默的父亲在灯塔附近失踪。",
                visibility="secret",
                known_by=("chen-mo",),
                source_event_id="submission:fog-harbor",
                introduced_at=datetime(2026, 7, 31, tzinfo=UTC),
            ),
            Fact(
                id="fact:storm-approaching",
                statement="暴风雨正在逼近。",
                visibility="private",
                known_by=("lin-lan",),
                source_event_id="submission:fog-harbor",
                introduced_at=datetime(2026, 7, 31, tzinfo=UTC),
            ),
        ),
    )


def _candidate() -> TurnCandidate:
    return TurnCandidate(
        id="turn-000001",
        project_id="fog-harbor",
        base_world_version=0,
        base_character_versions={"chen-mo": 0},
        intents=(
            CharacterIntent(
                character_id="chen-mo",
                action="检查灯芯槽",
                target="灯塔照明装置",
                goal="判断灯塔是否被人为关闭",
                knowledge_basis=("fact:lighthouse-never-off-at-night",),
            ),
        ),
        outcome=WorldOutcome(summary="陈默发现了新鲜刮痕。"),
    )


def _event() -> StoryEvent:
    return StoryEvent(
        id="event-000001",
        sequence=1,
        occurred_at=datetime(2026, 7, 31, 4, 0, tzinfo=UTC),
        summary="陈默发现了新鲜刮痕。",
        participants=("chen-mo",),
        source_turn_id="turn-000001",
        approved_by_user=True,
    )


def _formal_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*.md"))
        if ".story-engine" not in path.parts
    }


def test_project_store_creates_and_loads_markdown_workspace(tmp_path: Path) -> None:
    root = tmp_path / "fog-harbor"
    store = ProjectStore(root)

    store.create(_seed())
    snapshot = store.load()

    assert snapshot.project.id == "fog-harbor"
    assert snapshot.world.current_location == "雾港"
    assert [character.id for character in snapshot.characters] == [
        "chen-mo",
        "lin-lan",
    ]
    assert (root / "project.md").read_text(encoding="utf-8").startswith("---\n")
    assert (root / "characters/active/chen-mo.md").exists()
    assert (root / "events").is_dir()
    assert (root / "scenes").is_dir()


def test_candidate_store_never_changes_formal_markdown(tmp_path: Path) -> None:
    root = tmp_path / "fog-harbor"
    ProjectStore(root).create(_seed())
    before = _formal_bytes(root)

    CandidateStore(root).save(_candidate())

    assert _formal_bytes(root) == before
    candidate_path = root / ".story-engine/turns/turn-000001.json"
    assert candidate_path.exists()
    assert json.loads(candidate_path.read_text(encoding="utf-8"))["status"] == "draft"


def test_event_store_is_append_only(tmp_path: Path) -> None:
    root = tmp_path / "fog-harbor"
    ProjectStore(root).create(_seed())
    store = EventStore(root)

    event_path = store.append(_event())
    original = event_path.read_bytes()

    with pytest.raises(FileExistsError):
        store.append(_event())

    assert event_path.read_bytes() == original
    assert store.list_events() == (_event(),)


def test_project_load_does_not_depend_on_derived_index(tmp_path: Path) -> None:
    root = tmp_path / "fog-harbor"
    store = ProjectStore(root)
    store.create(_seed())
    index_path = store.rebuild_index()
    assert index_path.exists()

    shutil.rmtree(root / ".story-engine/index")
    snapshot = store.load()
    rebuilt_path = store.rebuild_index()

    assert snapshot.project.id == "fog-harbor"
    assert rebuilt_path.exists()
    assert json.loads(rebuilt_path.read_text(encoding="utf-8"))["characters"] == [
        "chen-mo",
        "lin-lan",
    ]
