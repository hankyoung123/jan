import json
import shutil
import time
from pathlib import Path
from threading import Event

from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.workspace.project_store import ProjectStore
from story_engine.workspace.session import WorkspaceSessionManager


def _project(tmp_path: Path) -> Path:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    return tmp_path / "fog-harbor"


def test_open_recovers_interrupted_commit_and_rebuilds_derived_state(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    original_world = (root / "world.md").read_bytes()
    recovery = root / ".story-engine/recovery/transaction-interrupted"
    backup = recovery / "backup"
    backup.mkdir(parents=True)
    (backup / "world.md").write_bytes(original_world)
    (recovery / "manifest.json").write_text(
        json.dumps(
            {
                "state": "prepared",
                "items": [{"path": "world.md", "existed": True}],
            }
        ),
        encoding="utf-8",
    )
    (root / "world.md").write_text("interrupted replacement", encoding="utf-8")
    shutil.rmtree(root / ".story-engine/cache")
    shutil.rmtree(root / ".story-engine/index")

    manager = WorkspaceSessionManager(tmp_path, start_watchers=False)
    state = manager.open("fog-harbor")

    assert state.status == "open"
    assert state.recovered_transactions == 1
    assert (root / "world.md").read_bytes() == original_world
    assert (root / ".story-engine/cache").is_dir()
    assert state.index.project_id == "fog-harbor"
    assert state.index.world_version == 0
    assert state.index.character_versions == {"chen-mo": 0, "lin-lan": 0}
    persisted = json.loads(
        (root / ".story-engine/index/project.json").read_text(encoding="utf-8")
    )
    assert persisted["revision"] == state.index.revision
    assert persisted["documents"][0]["relative_path"] == "project.md"


def test_watcher_refreshes_memory_index_after_external_markdown_change(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    changed = Event()
    changes = []
    manager = WorkspaceSessionManager(
        tmp_path,
        poll_interval=0.01,
        on_change=lambda change: (changes.append(change), changed.set()),
    )
    try:
        opened = manager.open("fog-harbor")
        world = ProjectStore(root).load().world.model_copy(
            update={"current_time": "暴风雨前夜稍晚", "version": 1}
        )

        ProjectStore(root).save_world(world)

        assert changed.wait(timeout=2)
        current = manager.get("fog-harbor")
        assert current.index.revision != opened.index.revision
        assert current.index.world_version == 1
        assert changes[-1].status == "ready"
        assert changes[-1].changed_paths == ("world.md",)
    finally:
        manager.close_all()


def test_watcher_preserves_last_valid_index_until_invalid_markdown_is_repaired(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    changed = Event()
    changes = []
    manager = WorkspaceSessionManager(
        tmp_path,
        poll_interval=0.01,
        on_change=lambda change: (changes.append(change), changed.set()),
    )
    try:
        opened = manager.open("fog-harbor")
        world_path = root / "world.md"
        original_world = world_path.read_text(encoding="utf-8")
        world_path.write_text(
            original_world.replace("version: 0", "version: invalid", 1),
            encoding="utf-8",
        )

        assert changed.wait(timeout=2)
        invalid = manager.get("fog-harbor")
        assert changes[-1].status == "error"
        assert invalid.index == opened.index
        assert invalid.last_error is not None

        changed.clear()
        world_path.write_text(original_world, encoding="utf-8")

        assert changed.wait(timeout=2)
        repaired = manager.get("fog-harbor")
        assert changes[-1].status == "ready"
        assert repaired.index == opened.index
        assert repaired.last_error is None
    finally:
        manager.close_all()


def test_close_releases_the_open_workspace_and_stops_notifications(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    changes = []
    manager = WorkspaceSessionManager(
        tmp_path,
        poll_interval=0.01,
        on_change=changes.append,
    )
    manager.open("fog-harbor")

    closed = manager.close("fog-harbor")
    world = ProjectStore(root).load().world.model_copy(update={"version": 1})
    ProjectStore(root).save_world(world)

    assert closed.status == "closed"
    assert manager.is_open("fog-harbor") is False
    assert changes == []


def test_project_catalog_comes_from_valid_canonical_markdown(tmp_path: Path) -> None:
    _project(tmp_path)
    invalid = tmp_path / "broken-project"
    invalid.mkdir()
    (invalid / "project.md").write_text("not front matter", encoding="utf-8")
    manager = WorkspaceSessionManager(tmp_path, start_watchers=False)

    projects = manager.list_projects()

    assert [(project.id, project.title, project.is_open) for project in projects] == [
        ("fog-harbor", "雾港", False)
    ]


def test_idle_watcher_uses_file_signatures_without_rebuilding_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _project(tmp_path)
    from story_engine.workspace import session as session_module

    real_build = session_module.build_workspace_index
    builds = 0

    def track_build(root: Path):
        nonlocal builds
        builds += 1
        return real_build(root)

    monkeypatch.setattr(session_module, "build_workspace_index", track_build)
    manager = WorkspaceSessionManager(
        tmp_path,
        poll_interval=0.01,
        debounce_interval=0.03,
    )
    try:
        manager.open("fog-harbor")
        time.sleep(0.08)
    finally:
        manager.close_all()

    assert builds == 1
