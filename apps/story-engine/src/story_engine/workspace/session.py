from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Literal

from pydantic import Field

from story_engine.domain.errors import DomainError
from story_engine.domain.models import DomainModel
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import (
    CharacterDocument,
    FactDocument,
    ProjectDocument,
    WorldDocument,
    load_document,
)
from story_engine.workspace.lock import ProjectLock
from story_engine.workspace.project_store import ProjectSnapshot, ProjectStore
from story_engine.workspace.transaction import recover_incomplete_transactions

_PROJECT_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DERIVED_DIRECTORIES = (
    ".story-engine/manuscript",
    ".story-engine/runtime/sessions",
    ".story-engine/cache",
    ".story-engine/index",
    ".story-engine/recovery",
)

WorkspaceDocumentKind = Literal["project", "world", "character", "fact"]


class WorkspaceNotOpenError(DomainError):
    """Raised when an operation requires an explicitly opened workspace."""


class WorkspaceDocumentEntry(DomainModel):
    relative_path: str = Field(min_length=1)
    kind: WorkspaceDocumentKind
    document_id: str = Field(min_length=1)
    version: int | None = Field(default=None, ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspaceIndex(DomainModel):
    project_id: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    world_version: int = Field(ge=0)
    character_versions: dict[str, int]
    fact_ids: tuple[str, ...]
    documents: tuple[WorkspaceDocumentEntry, ...] = Field(min_length=2)


class WorkspaceState(DomainModel):
    project: ProjectSnapshot
    index: WorkspaceIndex
    recovered_transactions: int = Field(ge=0)
    status: Literal["open"] = "open"
    last_error: str | None = None


class WorkspaceClosedState(DomainModel):
    project_id: str = Field(min_length=1)
    status: Literal["closed"] = "closed"


class WorkspaceChange(DomainModel):
    project_id: str = Field(min_length=1)
    status: Literal["ready", "error"]
    changed_paths: tuple[str, ...] = ()
    revision: str | None = None
    world_version: int | None = Field(default=None, ge=0)
    error: str | None = None


class ProjectCatalogEntry(DomainModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    genre: str = Field(min_length=1)
    world_version: int = Field(ge=0)
    is_open: bool


@dataclass(slots=True)
class _WorkspaceSession:
    state: WorkspaceState
    hashes: dict[str, str]
    signatures: dict[str, tuple[int, int]]
    stop: Event
    watcher: Thread | None = None


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_paths(root: Path) -> tuple[Path, ...]:
    paths = [root / "project.md", root / "world.md"]
    for directory in ("characters", "facts"):
        paths.extend((root / directory).rglob("*.md"))
    return tuple(
        sorted(
            (path for path in paths if path.is_file()),
            key=lambda path: path.relative_to(root).as_posix(),
        )
    )


def _file_signatures(root: Path) -> dict[str, tuple[int, int]]:
    return {
        path.relative_to(root).as_posix(): (
            path.stat().st_mtime_ns,
            path.stat().st_size,
        )
        for path in _canonical_paths(root)
    }


def _document(
    root: Path,
    path: Path,
    *,
    kind: WorkspaceDocumentKind,
    document_id: str,
    version: int | None,
) -> WorkspaceDocumentEntry:
    return WorkspaceDocumentEntry(
        relative_path=path.relative_to(root).as_posix(),
        kind=kind,
        document_id=document_id,
        version=version,
        sha256=_sha256(path),
    )


def build_workspace_index(root: Path) -> tuple[ProjectSnapshot, WorkspaceIndex]:
    """Validate canonical Markdown and build a disposable in-memory index."""

    with ProjectLock(root):
        return _build_workspace_index_locked(root)


def _build_workspace_index_locked(
    root: Path,
) -> tuple[ProjectSnapshot, WorkspaceIndex]:

    snapshot = ProjectStore(root).load()
    documents: list[WorkspaceDocumentEntry] = []

    project_path = root / "project.md"
    project, _ = load_document(project_path, ProjectDocument)
    documents.append(
        _document(
            root,
            project_path,
            kind="project",
            document_id=project.id,
            version=project.version,
        )
    )

    world_path = root / "world.md"
    world, _ = load_document(world_path, WorldDocument)
    documents.append(
        _document(
            root,
            world_path,
            kind="world",
            document_id="world",
            version=world.version,
        )
    )

    character_versions: dict[str, int] = {}
    for group in ("active", "npc", "retired"):
        for path in sorted((root / "characters" / group).glob("*.md")):
            character, _ = load_document(path, CharacterDocument)
            if character.id in character_versions:
                raise ValueError(f"duplicate character id: {character.id}")
            if character.type != group:
                raise ValueError(
                    f"character type does not match directory: {character.id}"
                )
            character_versions[character.id] = character.version
            documents.append(
                _document(
                    root,
                    path,
                    kind="character",
                    document_id=character.id,
                    version=character.version,
                )
            )

    fact_ids: list[str] = []
    for path in sorted((root / "facts").glob("*.md")):
        fact, _ = load_document(path, FactDocument)
        if fact.id in fact_ids:
            raise ValueError(f"duplicate fact id: {fact.id}")
        fact_ids.append(fact.id)
        documents.append(
            _document(
                root,
                path,
                kind="fact",
                document_id=fact.id,
                version=None,
            )
        )

    revision_input = "".join(
        f"{item.relative_path}\0{item.sha256}\n" for item in documents
    ).encode()
    revision = hashlib.sha256(revision_input).hexdigest()
    return snapshot, WorkspaceIndex(
        project_id=project.id,
        revision=revision,
        world_version=world.version,
        character_versions=character_versions,
        fact_ids=tuple(fact_ids),
        documents=tuple(documents),
    )


def canonical_revision(root: Path) -> str:
    """Return the validated revision of all canonical Markdown documents."""

    _snapshot, index = build_workspace_index(root)
    return index.revision


class WorkspaceSessionManager:
    """Own open project sessions, disposable indexes, and file watchers."""

    def __init__(
        self,
        projects_root: Path,
        *,
        poll_interval: float = 0.25,
        debounce_interval: float = 0.75,
        on_change: Callable[[WorkspaceChange], object] | None = None,
        start_watchers: bool = True,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll interval must be positive")
        if debounce_interval < poll_interval:
            raise ValueError("debounce interval must be at least poll interval")
        self.projects_root = projects_root
        self.poll_interval = poll_interval
        self.debounce_interval = debounce_interval
        self.on_change = on_change or (lambda _change: None)
        self.start_watchers = start_watchers
        self._sessions: dict[str, _WorkspaceSession] = {}
        self._lock = RLock()

    def _root(self, project_id: str) -> Path:
        if not _PROJECT_ID.fullmatch(project_id):
            raise FileNotFoundError(project_id)
        root = self.projects_root / project_id
        if not (root / "project.md").is_file():
            raise FileNotFoundError(project_id)
        return root

    @staticmethod
    def _ensure_derived_directories(root: Path) -> None:
        for relative in _DERIVED_DIRECTORIES:
            (root / relative).mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _persist(root: Path, index: WorkspaceIndex) -> None:
        content = json.dumps(
            index.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        atomic_write_text(root / ".story-engine/index/project.json", f"{content}\n")

    def open(self, project_id: str) -> WorkspaceState:
        with self._lock:
            existing = self._sessions.get(project_id)
            if existing is not None:
                return existing.state

        root = self._root(project_id)
        recovered = recover_incomplete_transactions(root)
        self._ensure_derived_directories(root)
        snapshot, index = build_workspace_index(root)
        if snapshot.project.id != project_id:
            raise ValueError("project id does not match workspace directory")
        self._persist(root, index)
        state = WorkspaceState(
            project=snapshot,
            index=index,
            recovered_transactions=recovered,
        )
        session = _WorkspaceSession(
            state=state,
            hashes={item.relative_path: item.sha256 for item in index.documents},
            signatures=_file_signatures(root),
            stop=Event(),
        )
        with self._lock:
            raced = self._sessions.get(project_id)
            if raced is not None:
                return raced.state
            self._sessions[project_id] = session
            if self.start_watchers:
                watcher = Thread(
                    target=self._watch,
                    args=(project_id, session),
                    name=f"story-workspace-{project_id}",
                    daemon=True,
                )
                session.watcher = watcher
                watcher.start()
        return state

    def get(self, project_id: str) -> WorkspaceState:
        with self._lock:
            session = self._sessions.get(project_id)
            if session is None:
                raise WorkspaceNotOpenError(f"workspace {project_id!r} is not open")
            return session.state

    def is_open(self, project_id: str) -> bool:
        with self._lock:
            return project_id in self._sessions

    def refresh(self, project_id: str) -> WorkspaceState:
        with self._lock:
            session = self._sessions.get(project_id)
            if session is None:
                raise WorkspaceNotOpenError(f"workspace {project_id!r} is not open")
        root = self._root(project_id)
        try:
            snapshot, index = build_workspace_index(root)
        except (OSError, ValueError) as error:
            should_emit = False
            with self._lock:
                current = self._sessions.get(project_id)
                if current is session:
                    should_emit = current.state.last_error != str(error)
                    current.state = current.state.model_copy(
                        update={"last_error": str(error)}
                    )
            if should_emit:
                self.on_change(
                    WorkspaceChange(
                        project_id=project_id,
                        status="error",
                        error=str(error),
                    )
                )
            raise

        hashes = {item.relative_path: item.sha256 for item in index.documents}
        changed_paths = tuple(
            sorted(
                path
                for path in set(session.hashes) | set(hashes)
                if session.hashes.get(path) != hashes.get(path)
            )
        )
        recovering = session.state.last_error is not None
        if not changed_paths and not recovering:
            # A file can change metadata without changing its canonical content.
            # Keep the watcher baseline current so that transient signatures do not
            # trigger a rebuild on every poll.
            with self._lock:
                current = self._sessions.get(project_id)
                if current is session:
                    current.signatures = _file_signatures(root)
            return session.state
        next_state = WorkspaceState(
            project=snapshot,
            index=index,
            recovered_transactions=session.state.recovered_transactions,
        )
        self._persist(root, index)
        with self._lock:
            current = self._sessions.get(project_id)
            if current is not session:
                raise WorkspaceNotOpenError(f"workspace {project_id!r} is not open")
            current.state = next_state
            current.hashes = hashes
            current.signatures = _file_signatures(root)
        if changed_paths or recovering:
            self.on_change(
                WorkspaceChange(
                    project_id=project_id,
                    status="ready",
                    changed_paths=changed_paths,
                    revision=index.revision,
                    world_version=index.world_version,
                )
            )
        return next_state

    def close(self, project_id: str) -> WorkspaceClosedState:
        with self._lock:
            session = self._sessions.pop(project_id, None)
        if session is not None:
            session.stop.set()
            if session.watcher is not None:
                session.watcher.join(timeout=max(1.0, self.poll_interval * 2))
        return WorkspaceClosedState(project_id=project_id)

    def close_all(self) -> None:
        with self._lock:
            project_ids = tuple(self._sessions)
        for project_id in project_ids:
            self.close(project_id)

    def list_projects(self) -> tuple[ProjectCatalogEntry, ...]:
        if not self.projects_root.exists():
            return ()
        projects: list[ProjectCatalogEntry] = []
        roots = sorted(path for path in self.projects_root.iterdir() if path.is_dir())
        for root in roots:
            invalid = (
                not _PROJECT_ID.fullmatch(root.name)
                or not (root / "project.md").is_file()
            )
            if invalid:
                continue
            try:
                snapshot = ProjectStore(root).load()
            except (OSError, ValueError):
                continue
            projects.append(
                ProjectCatalogEntry(
                    id=snapshot.project.id,
                    title=snapshot.project.title,
                    genre=snapshot.project.genre,
                    world_version=snapshot.world.version,
                    is_open=self.is_open(snapshot.project.id),
                )
            )
        return tuple(projects)

    def _watch(self, project_id: str, session: _WorkspaceSession) -> None:
        pending: dict[str, tuple[int, int]] | None = None
        deadline = 0.0
        while not session.stop.wait(self.poll_interval):
            root = self._root(project_id)
            try:
                current = _file_signatures(root)
            except (WorkspaceNotOpenError, FileNotFoundError):
                return
            except OSError:
                continue
            if current == session.signatures:
                pending = None
                continue
            if current != pending:
                pending = current
                deadline = time.monotonic() + self.debounce_interval
                continue
            if time.monotonic() < deadline:
                continue
            try:
                self.refresh(project_id)
            except (WorkspaceNotOpenError, FileNotFoundError):
                return
            except (OSError, ValueError):
                pending = None
                continue
            pending = None
