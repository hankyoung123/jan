import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from secrets import token_hex

from story_engine.workspace.atomic import atomic_write_text


def _replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)


@dataclass(frozen=True, slots=True)
class _PendingWrite:
    relative_path: PurePosixPath
    content: str
    overwrite: bool


class AtomicBatch:
    """A recoverable all-or-nothing batch for files below one project root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._writes: list[_PendingWrite] = []

    def add(
        self,
        relative_path: str,
        content: str,
        *,
        overwrite: bool = True,
    ) -> None:
        relative = PurePosixPath(relative_path)
        invalid = (
            relative.is_absolute()
            or ".." in relative.parts
            or relative == PurePosixPath(".")
        )
        if invalid:
            raise ValueError("transaction paths must stay below the project root")
        if any(item.relative_path == relative for item in self._writes):
            raise ValueError(f"duplicate transaction path: {relative}")
        self._writes.append(_PendingWrite(relative, content, overwrite))

    def commit(self) -> None:
        if not self._writes:
            return

        transaction_root = (
            self.root / ".story-engine/recovery" / f"transaction-{token_hex(8)}"
        )
        staged_root = transaction_root / "staged"
        backup_root = transaction_root / "backup"
        transaction_root.mkdir(parents=True, exist_ok=False)
        manifest_path = transaction_root / "manifest.json"
        items: list[dict[str, object]] = []

        try:
            for write in self._writes:
                relative = write.relative_path.as_posix()
                destination = self.root / relative
                if not write.overwrite and destination.exists():
                    raise FileExistsError(destination)

                staged = staged_root / relative
                atomic_write_text(staged, write.content, overwrite=False)
                existed = destination.exists()
                if existed:
                    backup = backup_root / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, backup)
                items.append({"path": relative, "existed": existed})

            manifest = {"state": "prepared", "items": items}
            atomic_write_text(
                manifest_path,
                f"{json.dumps(manifest, indent=2)}\n",
                overwrite=False,
            )

            try:
                for item in items:
                    relative = str(item["path"])
                    destination = self.root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    _replace(staged_root / relative, destination)
            except BaseException:
                self._rollback(items, backup_root)
                raise

            manifest["state"] = "committed"
            atomic_write_text(
                manifest_path,
                f"{json.dumps(manifest, indent=2)}\n",
            )
        finally:
            shutil.rmtree(transaction_root, ignore_errors=True)

    def _rollback(
        self,
        items: list[dict[str, object]],
        backup_root: Path,
    ) -> None:
        for item in reversed(items):
            relative = str(item["path"])
            destination = self.root / relative
            if bool(item["existed"]):
                _replace(backup_root / relative, destination)
            elif destination.exists():
                destination.unlink()


def recover_incomplete_transactions(root: Path) -> int:
    recovery_root = root / ".story-engine/recovery"
    if not recovery_root.exists():
        return 0

    recovered = 0
    for transaction_root in recovery_root.glob("transaction-*"):
        manifest_path = transaction_root / "manifest.json"
        if not manifest_path.exists():
            shutil.rmtree(transaction_root, ignore_errors=True)
            recovered += 1
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("state") != "committed":
            batch = AtomicBatch(root)
            batch._rollback(manifest["items"], transaction_root / "backup")
        shutil.rmtree(transaction_root, ignore_errors=True)
        recovered += 1
    return recovered
