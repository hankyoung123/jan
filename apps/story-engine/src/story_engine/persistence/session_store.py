import json
import re
from pathlib import Path
from typing import Any

from story_engine.domain.session_manifest import SessionManifest
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.lock import ProjectLock

_SESSION_ID = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")


class SessionStore:
    """Durable project-local index for every simulation session."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directory = root / ".story-engine/runtime/sessions"

    def _path(self, session_id: str) -> Path:
        if not _SESSION_ID.fullmatch(session_id):
            raise ValueError("invalid session ID")
        return self.directory / f"{session_id.replace(':', '__')}.json"

    @staticmethod
    def _content(manifest: SessionManifest) -> str:
        return (
            json.dumps(
                manifest.model_dump(mode="json"),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    def save(self, manifest: SessionManifest) -> Path:
        path = self._path(manifest.session_id)
        with ProjectLock(self.root):
            atomic_write_text(path, self._content(manifest))
        return path

    def load(self, session_id: str) -> SessionManifest:
        try:
            payload: Any = json.loads(
                self._path(session_id).read_text(encoding="utf-8")
            )
            manifest = SessionManifest.model_validate(payload)
        except FileNotFoundError:
            raise
        except (OSError, ValueError) as error:
            raise ValueError(f"session manifest {session_id!r} is invalid") from error
        if manifest.session_id != session_id:
            raise ValueError("session manifest ID mismatch")
        return manifest

    def list(self, project_id: str) -> tuple[SessionManifest, ...]:
        if not self.directory.exists():
            return ()
        manifests: list[SessionManifest] = []
        for path in self.directory.glob("*.json"):
            try:
                payload: Any = json.loads(path.read_text(encoding="utf-8"))
                manifest = SessionManifest.model_validate(payload)
            except (OSError, ValueError) as error:
                raise ValueError(
                    f"session manifest {path.name!r} is invalid"
                ) from error
            if manifest.project_id == project_id:
                manifests.append(manifest)
        return tuple(
            sorted(
                manifests,
                key=lambda item: (item.updated_at, item.session_id),
                reverse=True,
            )
        )
