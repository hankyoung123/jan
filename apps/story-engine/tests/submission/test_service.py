from pathlib import Path

import pytest

from story_engine.submission.service import (
    SubmissionNotRunnableError,
    SubmissionService,
    fog_harbor_submission,
)


def test_fog_harbor_submission_creates_runnable_project_without_outline(
    tmp_path: Path,
) -> None:
    package = fog_harbor_submission()

    snapshot = SubmissionService(tmp_path).finalize(package)
    root = tmp_path / "fog-harbor"

    assert snapshot.project.id == "fog-harbor"
    assert len(snapshot.characters) == 2
    assert all(character.current_goal for character in snapshot.characters)
    assert snapshot.world.active_pressures == ("客船即将进入近港航道",)
    assert snapshot.world.rules == (
        "灯塔控制港口夜航",
        "暴风雨时港口必须依赖灯塔或备用航标",
    )
    assert snapshot.world.world_variables["initial_incident"] == "灯塔突然熄灭"
    assert "灯塔控制港口夜航" in (root / "world.md").read_text(encoding="utf-8")
    assert not any("outline" in path.name.lower() for path in root.rglob("*"))
    assert "outline" not in (root / "project.md").read_text(encoding="utf-8").lower()


def test_submission_rejects_package_without_pressure_or_goal_conflict(
    tmp_path: Path,
) -> None:
    package = fog_harbor_submission().model_copy(
        update={
            "pressures": (),
            "characters": tuple(
                character.model_copy(update={"current_goal": "等待天亮"})
                for character in fog_harbor_submission().characters
            ),
        }
    )

    with pytest.raises(SubmissionNotRunnableError, match="pressure or conflicting"):
        SubmissionService(tmp_path).finalize(package)

    assert not (tmp_path / "fog-harbor").exists()
