import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from story_engine.domain.models import (
    Character,
    Fact,
    WorldState,
)
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
    assert not (root / "events").exists()
    assert not (root / "scenes").exists()
    assert (root / ".story-engine/runtime/sessions").is_dir()
    assert (root / ".story-engine/manuscript").is_dir()
    assert not (root / ".story-engine/projections").exists()


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
