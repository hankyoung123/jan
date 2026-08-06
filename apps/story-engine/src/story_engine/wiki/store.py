import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from story_engine.domain.wiki import (
    DirectorInstruction,
    WikiBranchView,
    WikiPage,
    WikiPageSummary,
    WikiPatch,
    WikiPatchOperation,
)
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import dump_json_envelope, load_json_envelope
from story_engine.workspace.project_store import ProjectSnapshot
from story_engine.workspace.transaction import AtomicBatch

_BRANCH_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,127}$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
_LINK = re.compile(r"\[[^\]]+\]\(([^)]+\.md(?:#[^)]*)?)\)")
_MAX_PAGE_CHARS = 65_536

SCHEMA_TEXT = """# Story Wiki Schema

The runtime log, checkpoints, and raw memory records are immutable sources. Wiki
pages are maintained interpretations and must never be treated as new events.

## Source boundaries

- World pages may use the project seed, director instructions, Game Master memory,
  and Game Master resolved events.
- A character page may use only that character's profile, actions, and private
  observations. It must never use another character's private memory.
- Every material conclusion must list its raw `source_ids` in front matter.
- Every page declares `visibility`: `public`, `private:<actor_id>`, or `gm_only`.
- Inferences must be labelled `belief`, `suspected`, or `uncertain`.
- Conflicts preserve the earlier understanding as history; they do not erase it.

## Maintenance rules

- Inspect the branch `index.md` before creating a page.
- Keep branch and directory indexes synchronized with page changes.
- Append every maintenance operation to branch `log.md`.
- Do not modify runtime logs, checkpoints, project seed documents, or raw memory.
- Wiki patches are restricted to `world/` or one subject under `characters/`.
"""


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_relative(path: str) -> PurePosixPath:
    relative = PurePosixPath(path)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or relative == PurePosixPath(".")
        or relative.suffix != ".md"
    ):
        raise ValueError("wiki path must be a relative Markdown path")
    if relative.parts[0] not in {"world", "characters"}:
        raise ValueError("wiki page must be below world/ or characters/")
    if relative.name in {"log.md"}:
        raise ValueError("wiki maintenance log is store-owned")
    return relative


def _render(page: WikiPage) -> str:
    metadata = dict(
        page.model_dump(
            mode="json",
            exclude={"content", "path"},
        )
    )
    if not metadata.get("content_hash"):
        metadata["content_hash"] = _content_hash(page.content)
    return (
        "---\n"
        + json.dumps(metadata, ensure_ascii=False, sort_keys=True)
        + "\n---\n\n"
        + page.content.rstrip()
        + "\n"
    )


def _parse(path: str, text: str) -> WikiPage:
    if not text.startswith("---\n"):
        raise ValueError(f"wiki page {path!r} has no front matter")
    try:
        raw_metadata, content = text[4:].split("\n---\n", 1)
        metadata = json.loads(raw_metadata)
    except (ValueError, json.JSONDecodeError) as error:
        raise ValueError(f"wiki page {path!r} has invalid front matter") from error
    page = WikiPage.model_validate(
        {**metadata, "path": path, "content": content.lstrip("\n").rstrip()}
    )
    if not page.content_hash:
        page = page.model_copy(update={"content_hash": _content_hash(page.content)})
    return page


class WikiRevisionConflictError(ValueError):
    def __init__(self, path: str, current_revision: int) -> None:
        super().__init__(
            f"wiki page {path!r} changed (current revision {current_revision})"
        )
        self.path = path
        self.current_revision = current_revision


def _title(content: str, fallback: str) -> str:
    match = _HEADING.search(content)
    return match.group(2).strip() if match else fallback


def _replace_section(content: str, section: str, replacement: str) -> str:
    matches = list(_HEADING.finditer(content))
    target = next(
        (match for match in matches if match.group(2).strip() == section.strip()),
        None,
    )
    if target is None:
        raise ValueError(f"wiki section {section!r} does not exist")
    level = len(target.group(1))
    end = len(content)
    for match in matches:
        if match.start() <= target.start():
            continue
        if len(match.group(1)) <= level:
            end = match.start()
            break
    heading = target.group(0)
    return (
        content[: target.start()]
        + heading
        + "\n\n"
        + replacement.strip()
        + "\n\n"
        + content[end:].lstrip("\n")
    ).rstrip()


class WikiStore:
    """Own Markdown Wiki persistence, indexing, history, and branch versions."""

    def __init__(self, root: Path, branch_id: str) -> None:
        if not _BRANCH_ID.fullmatch(branch_id):
            raise ValueError("invalid Wiki branch ID")
        self.root = root
        self.branch_id = branch_id
        self.wiki_root = root / "wiki"
        self.branch_root = self.wiki_root / "branches" / branch_id

    @property
    def schema_path(self) -> Path:
        return self.wiki_root / "SCHEMA.md"

    def page_path(self, relative_path: str) -> Path:
        relative = _validate_relative(relative_path)
        return self.branch_root / relative.as_posix()

    def _relative_to_project(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def exists(self) -> bool:
        return (self.branch_root / "index.md").is_file()

    def initialize(self, snapshot: ProjectSnapshot) -> tuple[Path, ...]:
        if self.exists():
            return tuple(self.branch_root.rglob("*.md"))
        source_id = f"project:{snapshot.project.id}"
        facts = {fact.id: fact for fact in snapshot.facts}
        world_sources = (source_id, *snapshot.world.public_fact_ids)
        world_state = "\n".join(
            (
                "# Current World State",
                "",
                f"- Time: {snapshot.world.current_time}",
                f"- Location: {snapshot.world.current_location or 'unknown'}",
                *(
                    f"- Pressure: {pressure}"
                    for pressure in snapshot.world.active_pressures
                ),
                *(
                    f"- Public fact: {facts[fact_id].statement}"
                    for fact_id in snapshot.world.public_fact_ids
                ),
            )
        )
        pages: list[WikiPage] = [
            WikiPage(
                branch_id=self.branch_id,
                path="world/state.md",
                updated_at_step=0,
                source_ids=world_sources,
                content=world_state,
            ),
            WikiPage(
                branch_id=self.branch_id,
                path="world/rules.md",
                updated_at_step=0,
                source_ids=world_sources,
                content="# World Rules\n\n"
                + "\n".join(f"- {rule}" for rule in snapshot.world.rules),
            ),
            WikiPage(
                branch_id=self.branch_id,
                path="world/threads.md",
                updated_at_step=0,
                source_ids=(source_id,),
                content="# Open Threads\n\n"
                + "\n".join(
                    f"- {pressure}" for pressure in snapshot.world.active_pressures
                ),
            ),
        ]
        for character in snapshot.characters:
            character_sources = (
                source_id,
                *(fact_id for fact_id in character.known_fact_ids),
            )
            pages.extend(
                (
                    WikiPage(
                        branch_id=self.branch_id,
                        path=f"characters/{character.id}/self.md",
                        subject_id=character.id,
                        visibility=f"private:{character.id}",
                        updated_at_step=0,
                        source_ids=character_sources,
                        content=(
                            f"# {character.display_name or character.id}\n\n"
                            f"## Identity\n\n{character.identity}\n\n"
                            f"## Core Desire\n\n{character.core_desire}"
                        ),
                    ),
                    WikiPage(
                        branch_id=self.branch_id,
                        path=f"characters/{character.id}/goals.md",
                        subject_id=character.id,
                        visibility=f"private:{character.id}",
                        updated_at_step=0,
                        source_ids=character_sources,
                        content=(
                            "# Goals\n\n## Current\n\n"
                            + (character.current_goal or character.core_desire)
                        ),
                    ),
                    WikiPage(
                        branch_id=self.branch_id,
                        path=f"characters/{character.id}/beliefs.md",
                        subject_id=character.id,
                        visibility=f"private:{character.id}",
                        updated_at_step=0,
                        source_ids=character_sources,
                        content="# Beliefs\n\n"
                        + "\n".join(
                            f"- {facts[fact_id].statement}"
                            for fact_id in character.known_fact_ids
                        ),
                    ),
                )
            )
        pages.extend(self._owned_indexes(pages, step=0, checkpoint_id=None).values())
        batch = AtomicBatch(self.root)
        if not self.schema_path.exists():
            batch.add("wiki/SCHEMA.md", SCHEMA_TEXT, overwrite=False)
        for page in pages:
            batch.add(
                self._relative_to_project(self.page_path(page.path)),
                _render(page),
                overwrite=False,
            )
            batch.add(
                f"wiki/.versions/{self.branch_id}/seed/{page.path}",
                _render(page),
                overwrite=False,
            )
        batch.add(
            self._relative_to_project(self.branch_root / "index.md"),
            self._render_index(pages, checkpoint_id=None, step=0, stale=False),
            overwrite=False,
        )
        batch.add(
            self._relative_to_project(self.branch_root / "log.md"),
            "# Wiki Maintenance Log\n",
            overwrite=False,
        )
        batch.add(
            f"wiki/.versions/{self.branch_id}/seed/index.md",
            self._render_index(pages, checkpoint_id=None, step=0, stale=False),
            overwrite=False,
        )
        batch.commit()
        return tuple(self.branch_root.rglob("*.md"))

    def load_page(self, relative_path: str) -> WikiPage:
        path = self.page_path(relative_path)
        return _parse(relative_path, path.read_text(encoding="utf-8"))

    def load_branch_index(self) -> WikiPage:
        return _parse(
            "world/index.md",
            (self.branch_root / "index.md").read_text(encoding="utf-8"),
        )

    def list_pages(self) -> tuple[WikiPage, ...]:
        if not self.branch_root.exists():
            return ()
        pages = []
        for directory in ("world", "characters"):
            for path in sorted((self.branch_root / directory).rglob("*.md")):
                relative = path.relative_to(self.branch_root).as_posix()
                pages.append(_parse(relative, path.read_text(encoding="utf-8")))
        return tuple(pages)

    @staticmethod
    def is_model_editable_path(relative_path: str) -> bool:
        try:
            relative = _validate_relative(relative_path)
        except ValueError:
            return False
        return relative.name not in {"index.md", "log.md", "SCHEMA.md"}

    def model_editable_pages(self) -> tuple[WikiPage, ...]:
        """Return only content pages that a Wiki Agent may propose updates for."""

        pages = []
        for page in self.list_pages():
            if self.is_model_editable_path(page.path):
                pages.append(page)
        return tuple(pages)

    def version_root(self, version_id: str) -> Path:
        if version_id != "seed" and (
            not version_id.startswith("checkpoint-") or len(version_id) != 75
        ):
            raise ValueError("invalid Wiki version ID")
        return self.wiki_root / ".versions" / self.branch_id / version_id

    def version_exists(self, version_id: str) -> bool:
        return (self.version_root(version_id) / "index.md").is_file()

    def list_version_pages(self, version_id: str) -> tuple[WikiPage, ...]:
        root = self.version_root(version_id)
        if not (root / "index.md").is_file():
            raise FileNotFoundError(root)
        pages = []
        for path in sorted(root.rglob("*.md")):
            if path.parent == root:
                continue
            relative = path.relative_to(root).as_posix()
            pages.append(_parse(relative, path.read_text(encoding="utf-8")))
        return tuple(pages)

    def view(self) -> WikiBranchView:
        index = _parse(
            "world/index.md",
            (self.branch_root / "index.md").read_text(encoding="utf-8"),
        )
        return WikiBranchView(
            branch_id=self.branch_id,
            checkpoint_id=index.checkpoint_id,
            updated_at_step=index.updated_at_step,
            stale=index.stale,
            degraded=index.degraded,
            degradation_reason=index.degradation_reason,
            pages=tuple(
                WikiPageSummary(
                    path=page.path,
                    title=_title(page.content, PurePosixPath(page.path).stem),
                    subject_id=page.subject_id,
                    updated_at_step=page.updated_at_step,
                    source_ids=page.source_ids,
                    confidence=page.confidence,
                    visibility=page.visibility,
                )
                for page in self.list_pages()
            ),
        )

    def _render_index(
        self,
        pages: list[WikiPage] | tuple[WikiPage, ...],
        *,
        checkpoint_id: str | None,
        step: int,
        stale: bool,
        degraded: bool = False,
        degradation_reason: str | None = None,
    ) -> str:
        links = "\n".join(
            f"- [{_title(page.content, PurePosixPath(page.path).stem)}]({page.path})"
            for page in sorted(pages, key=lambda item: item.path)
        )
        index = WikiPage(
            branch_id=self.branch_id,
            path="world/index.md",
            updated_at_step=step,
            checkpoint_id=checkpoint_id,
            stale=stale,
            degraded=degraded,
            degradation_reason=degradation_reason,
            content=f"# Branch {self.branch_id} Wiki\n\n{links}",
        )
        return _render(index)

    def _owned_indexes(
        self,
        pages: list[WikiPage] | tuple[WikiPage, ...],
        *,
        step: int,
        checkpoint_id: str | None,
    ) -> dict[str, WikiPage]:
        indexes: dict[str, WikiPage] = {}
        groups: dict[tuple[str, str | None], list[WikiPage]] = {}
        for page in pages:
            if page.path.endswith("/index.md"):
                continue
            parts = PurePosixPath(page.path).parts
            key = ("world", None) if parts[0] == "world" else (parts[1], parts[1])
            groups.setdefault(key, []).append(page)
        for (directory, subject_id), members in groups.items():
            if subject_id is None:
                path = "world/index.md"
                title = "# World Wiki"
                base = PurePosixPath("world")
            else:
                path = f"characters/{directory}/index.md"
                title = f"# Character Wiki: {directory}"
                base = PurePosixPath("characters", directory)
            links = "\n".join(
                f"- [{_title(page.content, PurePosixPath(page.path).stem)}]"
                f"({PurePosixPath(page.path).relative_to(base).as_posix()})"
                for page in sorted(members, key=lambda item: item.path)
            )
            indexes[path] = WikiPage(
                branch_id=self.branch_id,
                path=path,
                subject_id=subject_id,
                visibility=(f"private:{subject_id}" if subject_id else "public"),
                updated_at_step=step,
                source_ids=tuple(
                    dict.fromkeys(
                        source_id for page in members for source_id in page.source_ids
                    )
                ),
                checkpoint_id=checkpoint_id,
                content=f"{title}\n\n{links}",
            )
        return indexes

    def _apply_to_page(
        self,
        page: WikiPage | None,
        patch: WikiPatch,
        step: int,
    ) -> WikiPage:
        if patch.operation == WikiPatchOperation.CREATE:
            if page is not None:
                raise ValueError(f"wiki page {patch.path!r} already exists")
            subject = (
                PurePosixPath(patch.path).parts[1]
                if PurePosixPath(patch.path).parts[0] == "characters"
                else None
            )
            return WikiPage(
                branch_id=self.branch_id,
                path=patch.path,
                subject_id=subject,
                updated_at_step=step,
                source_ids=patch.source_ids,
                confidence=patch.confidence,
                visibility=(
                    patch.visibility
                    or (f"private:{subject}" if subject is not None else "public")
                ),
                content=patch.content,
                revision=0,
                content_hash=_content_hash(patch.content),
            )
        if page is None:
            raise FileNotFoundError(self.page_path(patch.path))
        if patch.operation == WikiPatchOperation.APPEND_HISTORY:
            content = page.content.rstrip() + "\n\n" + patch.content.strip()
        elif patch.operation == WikiPatchOperation.REPLACE_SECTION:
            assert patch.section is not None
            content = _replace_section(page.content, patch.section, patch.content)
        else:
            assert patch.section is not None
            archived = "Archived understanding:\n\n" + patch.content.strip()
            content = _replace_section(page.content, patch.section, archived)
        if len(content) > _MAX_PAGE_CHARS:
            raise ValueError("Wiki page exceeds the page length limit")
        return page.model_copy(
            update={
                "updated_at_step": step,
                "source_ids": tuple(
                    dict.fromkeys((*page.source_ids, *patch.source_ids))
                ),
                "confidence": patch.confidence,
                **(
                    {"visibility": patch.visibility}
                    if patch.visibility is not None
                    else {}
                ),
                "content": content,
                "revision": page.revision + 1,
                "content_hash": _content_hash(content),
            }
        )

    def apply_patches(
        self,
        patches: tuple[WikiPatch, ...],
        *,
        checkpoint_id: str,
        step: int,
    ) -> tuple[Path, ...]:
        if not patches:
            return ()
        pages = {page.path: page for page in self.list_pages()}
        changed: dict[str, WikiPage] = {}
        for patch in patches:
            _validate_relative(patch.path)
            original = pages.get(patch.path)
            if patch.operation == WikiPatchOperation.CREATE:
                if original is not None:
                    raise ValueError(f"wiki page {patch.path!r} already exists")
            else:
                if original is None:
                    raise FileNotFoundError(self.page_path(patch.path))
                if patch.expected_revision is None:
                    raise ValueError(
                        f"wiki patch for {patch.path!r} must declare expected_revision"
                    )
                if patch.expected_revision != original.revision:
                    raise WikiRevisionConflictError(patch.path, original.revision)
                if (
                    patch.expected_content_hash
                    and patch.expected_content_hash != original.content_hash
                ):
                    raise WikiRevisionConflictError(patch.path, original.revision)
            current = changed.get(patch.path, original)
            changed[patch.path] = self._apply_to_page(current, patch, step)
        candidate = {**pages, **changed}
        owned_indexes = {
            path: self._bump_revision(index, pages.get(path))
            for path, index in self._owned_indexes(
                list(candidate.values()),
                step=step,
                checkpoint_id=checkpoint_id,
            ).items()
        }
        candidate.update(owned_indexes)
        changed.update(owned_indexes)
        self._validate_links(candidate)
        batch = AtomicBatch(self.root)
        for relative, page in changed.items():
            batch.add(
                self._relative_to_project(self.page_path(relative)),
                _render(page.model_copy(update={"checkpoint_id": checkpoint_id})),
                overwrite=relative in pages,
            )
        batch.add(
            self._relative_to_project(self.branch_root / "index.md"),
            self._render_index(
                list(candidate.values()),
                checkpoint_id=checkpoint_id,
                step=step,
                stale=False,
            ),
        )
        existing_log = (self.branch_root / "log.md").read_text(encoding="utf-8")
        entries = "\n".join(
            f"- step {step}: `{patch.operation.value}` `{patch.path}` "
            + (
                f"page_id={patch.proposal_page_id} "
                "source_refs="
                + ",".join(str(item) for item in patch.proposal_source_refs)
                + " "
                if patch.proposal_page_id is not None
                else ""
            )
            + f"sources={','.join(patch.source_ids)}"
            for patch in patches
        )
        batch.add(
            self._relative_to_project(self.branch_root / "log.md"),
            existing_log.rstrip() + "\n" + entries + "\n",
        )
        version_root = self.wiki_root / ".versions" / self.branch_id / checkpoint_id
        for relative, page in candidate.items():
            version_path = version_root / relative
            batch.add(
                self._relative_to_project(version_path),
                _render(page.model_copy(update={"checkpoint_id": checkpoint_id})),
                overwrite=version_path.exists(),
            )
        batch.add(
            self._relative_to_project(version_root / "index.md"),
            self._render_index(
                list(candidate.values()),
                checkpoint_id=checkpoint_id,
                step=step,
                stale=False,
            ),
            overwrite=(version_root / "index.md").exists(),
        )
        batch.commit()
        return tuple(self.page_path(path) for path in changed)

    @staticmethod
    def _bump_revision(page: WikiPage, current: WikiPage | None) -> WikiPage:
        return page.model_copy(
            update={
                "revision": 0 if current is None else current.revision + 1,
                "content_hash": _content_hash(page.content),
            }
        )

    def save_page(
        self,
        relative_path: str,
        content: str,
        expected_revision: int,
    ) -> WikiPage:
        """Apply a user edit with an optimistic revision guard."""
        _validate_relative(relative_path)
        current = self.load_page(relative_path)
        if expected_revision != current.revision:
            raise WikiRevisionConflictError(relative_path, current.revision)
        if len(content) > _MAX_PAGE_CHARS:
            raise ValueError("Wiki page exceeds the page length limit")
        updated = current.model_copy(
            update={
                "content": content.rstrip(),
                "revision": current.revision + 1,
                "content_hash": _content_hash(content.rstrip()),
            }
        )
        pages = {page.path: page for page in self.list_pages()}
        pages[updated.path] = updated
        indexes = {
            path: self._bump_revision(index, pages.get(path))
            for path, index in self._owned_indexes(
                list(pages.values()),
                step=current.updated_at_step,
                checkpoint_id=current.checkpoint_id,
            ).items()
        }
        pages.update(indexes)
        self._validate_links(pages)
        batch = AtomicBatch(self.root)
        batch.add(
            self._relative_to_project(self.page_path(updated.path)),
            _render(updated),
            overwrite=True,
        )
        for index_path, index in indexes.items():
            batch.add(
                self._relative_to_project(self.page_path(index_path)),
                _render(index),
                overwrite=True,
            )
        existing_log = (self.branch_root / "log.md").read_text(encoding="utf-8")
        batch.add(
            self._relative_to_project(self.branch_root / "log.md"),
            existing_log.rstrip()
            + f"\n- manual edit: `{updated.path}` revision {updated.revision}\n",
        )
        batch.commit()
        return updated

    def set_head(
        self,
        checkpoint_id: str,
        step: int,
        *,
        stale: bool,
        degraded: bool = False,
        degradation_reason: str | None = None,
    ) -> None:
        pages = list(self.list_pages())
        atomic_write_text(
            self.branch_root / "index.md",
            self._render_index(
                pages,
                checkpoint_id=checkpoint_id,
                step=step,
                stale=stale,
                degraded=degraded,
                degradation_reason=degradation_reason,
            ),
        )

    def mark_stale(self, checkpoint_id: str, step: int) -> None:
        self.set_head(checkpoint_id, step, stale=True)

    def mark_degraded(self, checkpoint_id: str, step: int, reason: str) -> None:
        self.set_head(
            checkpoint_id,
            step,
            stale=True,
            degraded=True,
            degradation_reason=reason,
        )

    def fork_from(
        self,
        *,
        parent_branch_id: str,
        checkpoint_id: str,
        checkpoint_step: int,
    ) -> None:
        if self.exists():
            raise FileExistsError(self.branch_root)
        versions_root = self.wiki_root / ".versions" / parent_branch_id
        candidates: list[tuple[int, Path]] = []
        if versions_root.exists():
            for index_path in versions_root.glob("*/index.md"):
                page = _parse("world/index.md", index_path.read_text(encoding="utf-8"))
                if page.updated_at_step <= checkpoint_step:
                    candidates.append((page.updated_at_step, index_path.parent))
        if candidates:
            source_root = max(candidates, key=lambda item: item[0])[1]
        else:
            source_root = self.wiki_root / "branches" / parent_branch_id
        if not source_root.exists():
            raise FileNotFoundError(source_root)
        for source in source_root.rglob("*.md"):
            relative = source.relative_to(source_root)
            destination = self.branch_root / relative
            if relative == Path("index.md"):
                continue
            text = source.read_text(encoding="utf-8")
            if relative != Path("log.md"):
                page = _parse(relative.as_posix(), text)
                text = _render(
                    page.model_copy(
                        update={
                            "branch_id": self.branch_id,
                            "checkpoint_id": checkpoint_id,
                        }
                    )
                )
            atomic_write_text(destination, text, overwrite=False)
        log_path = self.branch_root / "log.md"
        if not log_path.exists():
            atomic_write_text(
                log_path,
                f"# Wiki Maintenance Log\n\n- forked from `{parent_branch_id}` "
                f"at `{checkpoint_id}`\n",
                overwrite=False,
            )
        pages = list(self.list_pages())
        atomic_write_text(
            self.branch_root / "index.md",
            self._render_index(
                pages,
                checkpoint_id=checkpoint_id,
                step=min(
                    checkpoint_step,
                    max((page.updated_at_step for page in pages), default=0),
                ),
                stale=False,
            ),
            overwrite=False,
        )

    def list_instructions(self) -> tuple[DirectorInstruction, ...]:
        directory = self.branch_root / "director-instructions"
        if not directory.exists():
            return ()
        instructions = tuple(
            DirectorInstruction.model_validate(
                load_json_envelope(
                    path,
                    schema="story-engine/director-instruction/v1",
                )
            )
            for path in directory.glob("*.md")
        )
        return tuple(sorted(instructions, key=lambda item: item.created_at))

    def add_instruction(self, instruction: DirectorInstruction) -> Path:
        path = (
            self.branch_root
            / "director-instructions"
            / f"{quote(instruction.instruction_id, safe='')}.md"
        )
        atomic_write_text(
            path,
            dump_json_envelope(
                schema="story-engine/director-instruction/v1",
                title=f"Director Instruction {instruction.instruction_id}",
                metadata={
                    "instruction_id": instruction.instruction_id,
                    "branch_id": self.branch_id,
                    "checkpoint_id": instruction.applies_from_checkpoint_id,
                },
                body=f"# Director Instruction\n\n{instruction.text}",
                payload=instruction.model_dump(mode="json"),
            ),
            overwrite=False,
        )
        return path

    @staticmethod
    def _validate_links(pages: dict[str, WikiPage]) -> None:
        available = {PurePosixPath(path) for path in pages}
        for page in pages.values():
            parent = PurePosixPath(page.path).parent
            for target in _LINK.findall(page.content):
                clean = target.split("#", 1)[0]
                resolved = PurePosixPath(parent, clean)
                normalized = PurePosixPath(
                    *(part for part in resolved.parts if part not in {"."})
                )
                if ".." in normalized.parts or normalized not in available:
                    raise ValueError(
                        f"wiki page {page.path!r} links to missing page {target!r}"
                    )

    def clear(self) -> None:
        """Test-only convenience for disposable project roots."""
        if self.branch_root.exists():
            shutil.rmtree(self.branch_root)
