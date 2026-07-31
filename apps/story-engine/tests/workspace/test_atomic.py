from pathlib import Path

import pytest

from story_engine.workspace.atomic import atomic_write_text


def test_atomic_write_replaces_content_without_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "world.md"
    target.write_text("old", encoding="utf-8")

    atomic_write_text(target, "new")

    assert target.read_text(encoding="utf-8") == "new"
    assert list(tmp_path.iterdir()) == [target]


def test_atomic_create_refuses_to_overwrite_existing_file(tmp_path: Path) -> None:
    target = tmp_path / "events" / "000001.md"
    atomic_write_text(target, "original", overwrite=False)

    with pytest.raises(FileExistsError):
        atomic_write_text(target, "replacement", overwrite=False)

    assert target.read_text(encoding="utf-8") == "original"

