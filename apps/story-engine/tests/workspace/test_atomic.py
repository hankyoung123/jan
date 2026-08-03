from pathlib import Path

import pytest

from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.transaction import (
    AtomicBatch,
    recover_incomplete_transactions,
)


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


def test_atomic_batch_can_move_a_canonical_document(tmp_path: Path) -> None:
    source = tmp_path / "characters/npc/pilot.md"
    source.parent.mkdir(parents=True)
    source.write_text("npc", encoding="utf-8")
    batch = AtomicBatch(tmp_path)
    batch.add("characters/active/pilot.md", "active", overwrite=False)
    batch.delete("characters/npc/pilot.md")

    batch.commit()

    assert not source.exists()
    assert (tmp_path / "characters/active/pilot.md").read_text(
        encoding="utf-8"
    ) == "active"
    assert not any((tmp_path / ".story-engine/recovery").iterdir())


def test_atomic_batch_restores_deleted_document_after_late_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "characters/npc/pilot.md"
    source.parent.mkdir(parents=True)
    source.write_text("npc", encoding="utf-8")
    batch = AtomicBatch(tmp_path)
    batch.add("characters/active/pilot.md", "active", overwrite=False)
    batch.delete("characters/npc/pilot.md")
    batch.add("events/000001.md", "event", overwrite=False)

    from story_engine.workspace import transaction

    real_replace = transaction._replace
    replacements = 0

    def fail_second_write(source_path: Path, destination: Path) -> None:
        nonlocal replacements
        replacements += 1
        if replacements == 2:
            raise OSError("simulated late write failure")
        real_replace(source_path, destination)

    monkeypatch.setattr(transaction, "_replace", fail_second_write)

    with pytest.raises(OSError, match="simulated late write failure"):
        batch.commit()

    assert source.read_text(encoding="utf-8") == "npc"
    assert not (tmp_path / "characters/active/pilot.md").exists()
    assert not (tmp_path / "events/000001.md").exists()
    assert not any((tmp_path / ".story-engine/recovery").iterdir())


def test_project_open_recovers_a_prepared_transaction(tmp_path: Path) -> None:
    target = tmp_path / "runtime/session.md"
    target.parent.mkdir(parents=True)
    target.write_text("partially committed", encoding="utf-8")
    transaction_root = tmp_path / ".story-engine/recovery/transaction-crash"
    backup = transaction_root / "backup/runtime/session.md"
    backup.parent.mkdir(parents=True)
    backup.write_text("before", encoding="utf-8")
    (transaction_root / "manifest.json").write_text(
        '{"state":"prepared","items":['
        '{"path":"runtime/session.md","existed":true,"operation":"write"}'
        "]}\n",
        encoding="utf-8",
    )

    assert recover_incomplete_transactions(tmp_path) == 1

    assert target.read_text(encoding="utf-8") == "before"
    assert not transaction_root.exists()
