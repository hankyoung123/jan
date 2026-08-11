import re
from pathlib import Path, PurePosixPath

from story_engine.domain.wiki import (
    WikiLintIssue,
    WikiLintResult,
    WikiLintSeverity,
    WikiPage,
)
from story_engine.persistence.branch_store import BranchStore
from story_engine.persistence.checkpoint_store import CheckpointStore
from story_engine.persistence.simulation_log import SimulationLogStore
from story_engine.wiki.store import WikiStore
from story_engine.workspace.project_store import ProjectStore

_LINK = re.compile(r"\[[^\]]+\]\(([^)]+\.md)(?:#[^)]*)?\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


class WikiLinter:
    """Deterministic consistency checks over one branch's Wiki."""

    def __init__(
        self,
        root: Path,
        branch_id: str,
        *,
        max_page_chars: int = 65_536,
    ) -> None:
        self.store = WikiStore(root, branch_id)
        self.root = root
        self.branch_id = branch_id
        self.max_page_chars = max_page_chars
        self._logs = SimulationLogStore(root)

    @staticmethod
    def _issue(
        code: str,
        message: str,
        path: str | None,
        severity: WikiLintSeverity,
    ) -> WikiLintIssue:
        return WikiLintIssue(
            code=code,
            message=message,
            path=path,
            severity=severity,
        )

    @staticmethod
    def _page_subject(page: WikiPage) -> str | None:
        parts = PurePosixPath(page.path).parts
        return parts[1] if len(parts) > 1 and parts[0] == "characters" else None

    def run(self) -> WikiLintResult:
        pages = self.store.list_pages()
        paths = {page.path for page in pages}
        linked: set[str] = set()
        issues: list[WikiLintIssue] = []
        for page in pages:
            if len(page.content) > self.max_page_chars:
                issues.append(
                    self._issue(
                        "page-too-long",
                        "Wiki page exceeds the bounded context page limit",
                        page.path,
                        WikiLintSeverity.ERROR,
                    )
                )
            if not page.source_ids and page.updated_at_step > 0:
                issues.append(
                    self._issue(
                        "missing-sources",
                        "Maintained Wiki page has no source IDs",
                        page.path,
                        WikiLintSeverity.ERROR,
                    )
                )
            for target in _LINK.findall(page.content):
                resolved = str(PurePosixPath(PurePosixPath(page.path).parent, target))
                linked.add(resolved)
                if resolved not in paths:
                    issues.append(
                        self._issue(
                            "missing-link",
                            f"Wiki link points to missing page {target}",
                            page.path,
                            WikiLintSeverity.ERROR,
                        )
                    )
        for page in pages:
            if (
                page.path not in linked
                and page.path
                not in {"world/state.md", "world/rules.md", "world/threads.md"}
                and not page.path.endswith("/self.md")
            ):
                issues.append(
                    self._issue(
                        "orphan-page",
                        "Wiki page is reachable only from the branch index",
                        page.path,
                        WikiLintSeverity.WARNING,
                    )
                )
        self._check_sources(pages, issues)
        self._check_head(pages, issues)
        self._check_duplicates(pages, issues)
        return WikiLintResult(
            branch_id=self.branch_id,
            passed=not any(
                issue.severity == WikiLintSeverity.ERROR for issue in issues
            ),
            issues=tuple(issues),
        )

    def _check_sources(
        self,
        pages: tuple[WikiPage, ...],
        issues: list[WikiLintIssue],
    ) -> None:
        for page in pages:
            page_subject = self._page_subject(page)
            for source_id in page.source_ids:
                located = self._locate_source(source_id)
                if located is None:
                    issues.append(
                        self._issue(
                            "source-not-found",
                            f"Wiki source {source_id!r} cannot be opened as raw "
                            "Markdown",
                            page.path,
                            WikiLintSeverity.ERROR,
                        )
                    )
                    continue
                branch, subject, step, kind = located
                if branch != self.branch_id:
                    issues.append(
                        self._issue(
                            "source-wrong-branch",
                            f"Wiki source {source_id!r} belongs to branch "
                            f"{branch!r}",
                            page.path,
                            WikiLintSeverity.ERROR,
                        )
                    )
                    continue
                if step is not None and step > page.updated_at_step:
                    issues.append(
                        self._issue(
                            "source-after-wiki-step",
                            f"Wiki source {source_id!r} is from step {step}, "
                            f"after page step {page.updated_at_step}",
                            page.path,
                            WikiLintSeverity.ERROR,
                        )
                    )
                if page_subject is None:
                    if subject is not None:
                        issues.append(
                            self._issue(
                                "private-source-leak",
                                f"World page cites private source {source_id!r} "
                                f"of {subject!r}",
                                page.path,
                                WikiLintSeverity.ERROR,
                            )
                        )
                elif subject is not None and subject != page_subject:
                    code = (
                        "source-subject-mismatch"
                        if kind == "profile"
                        else "private-source-leak"
                    )
                    issues.append(
                        self._issue(
                            code,
                            f"Wiki source {source_id!r} belongs to "
                            f"{subject!r}, not {page_subject!r}",
                            page.path,
                            WikiLintSeverity.ERROR,
                        )
                    )

    def _locate_source(
        self,
        source_id: str,
    ) -> tuple[str, str | None, int | None, str] | None:
        project = ProjectStore(self.root).load()
        if source_id.startswith("project:"):
            if source_id == f"project:{project.project.id}":
                return self.branch_id, None, 0, "project"
            return None
        if source_id.startswith("profile:"):
            actor_id = source_id.removeprefix("profile:")
            if any(character.id == actor_id for character in project.characters):
                return self.branch_id, actor_id, 0, "profile"
            return None
        fact = next(
            (item for item in project.facts if item.id == source_id),
            None,
        )
        if fact is not None:
            subject = fact.known_by[0] if fact.known_by else None
            return (
                self.branch_id,
                subject,
                0,
                "profile" if subject is not None else "project",
            )
        try:
            branch = BranchStore(self.root).load(self.branch_id)
        except FileNotFoundError:
            return None
        if branch.head_checkpoint_id is None:
            return None
        checkpoints = CheckpointStore(self.root)
        memories = self._logs.reachable_memory_records(
            checkpoints,
            branch.head_checkpoint_id,
            branch_id=self.branch_id,
        )
        memory = next(
            (record for record in memories if record.record_id == source_id),
            None,
        )
        if memory is not None:
            if "director_instruction" in memory.tags:
                return self.branch_id, None, memory.step, "director"
            return self.branch_id, memory.owner_id, memory.step, "observation"
        records = self._logs.reachable(
            checkpoints,
            branch.head_checkpoint_id,
            branch_id=self.branch_id,
        )
        for record in records:
            trace = record.trace
            action_source = f"action:{record.result.session_id}:{record.result.step}"
            candidates = {
                trace.trace_id,
                trace.putative_event_record_id,
                *trace.resolved_event_record_ids,
            }
            if record.result.action_text:
                candidates.add(action_source)
            if record.result.resolved_turn is not None:
                candidates.update(
                    event.event_id for event in record.result.resolved_turn.events
                )
            if source_id in candidates:
                subject = (
                    record.result.acting_actor_id
                    if source_id == action_source
                    else None
                )
                return self.branch_id, subject, record.result.step, "raw"
        return None

    def _check_head(
        self,
        pages: tuple[WikiPage, ...],
        issues: list[WikiLintIssue],
    ) -> None:
        del pages
        if not (self.store.branch_root / "index.md").is_file():
            return
        try:
            index = self.store.load_branch_index()
            branch = BranchStore(self.root).load(self.branch_id)
        except FileNotFoundError:
            return
        head = branch.head_checkpoint_id
        head_step = branch.head_step
        if index.updated_at_step < head_step:
            issues.append(
                self._issue(
                    "wiki-behind-branch",
                    f"Wiki step {index.updated_at_step} is behind branch head "
                    f"step {head_step}",
                    "world/index.md",
                    WikiLintSeverity.ERROR,
                )
            )
        elif index.updated_at_step > head_step:
            issues.append(
                self._issue(
                    "wiki-ahead-of-branch",
                    f"Wiki step {index.updated_at_step} is ahead of branch head "
                    f"step {head_step}",
                    "world/index.md",
                    WikiLintSeverity.ERROR,
                )
            )
        if (index.stale and index.checkpoint_id == head) or (
            not index.stale and index.checkpoint_id != head
        ):
            issues.append(
                self._issue(
                    "stale-flag-mismatch",
                    "Wiki stale flag does not match the branch head",
                    "world/index.md",
                    WikiLintSeverity.ERROR,
                )
            )

    def _check_duplicates(
        self,
        pages: tuple[WikiPage, ...],
        issues: list[WikiLintIssue],
    ) -> None:
        self_pages: dict[str | None, list[str]] = {}
        for page in pages:
            if page.path.endswith("/self.md"):
                self_pages.setdefault(page.subject_id, []).append(page.path)
        for subject, paths in self_pages.items():
            if len(paths) > 1:
                issues.append(
                    self._issue(
                        "duplicate-self-page",
                        f"subject {subject!r} has multiple self pages: "
                        f"{', '.join(sorted(paths))}",
                        sorted(paths)[0],
                        WikiLintSeverity.ERROR,
                    )
                )

        location_pages = [
            page
            for page in pages
            if PurePosixPath(page.path).parent.as_posix() == "world/locations"
        ]
        titles: dict[str, list[str]] = {}
        for page in location_pages:
            match = _HEADING.search(page.content)
            title = (
                match.group(2).strip()
                if match
                else PurePosixPath(page.path).stem
            )
            titles.setdefault(title.casefold(), []).append(page.path)
        for title, paths in titles.items():
            if len(paths) > 1:
                issues.append(
                    self._issue(
                        "duplicate-location-page",
                        f"location {title!r} has multiple pages: "
                        f"{', '.join(sorted(paths))}",
                        sorted(paths)[0],
                        WikiLintSeverity.ERROR,
                    )
                )

        relationship_pages = [
            page
            for page in pages
            if PurePosixPath(page.path).parent.as_posix() == "world/relationships"
        ]
        stems = {
            PurePosixPath(page.path).stem: page.path for page in relationship_pages
        }
        characters = [
            character.id
            for character in ProjectStore(self.root).load().characters
        ]
        for left in characters:
            for right in characters:
                if left >= right:
                    continue
                forward = f"{left}-{right}"
                backward = f"{right}-{left}"
                if forward in stems and backward in stems:
                    issues.append(
                        self._issue(
                            "duplicate-relationship-direction",
                            f"relationship {forward!r} duplicates "
                            f"{backward!r}",
                            stems[backward],
                            WikiLintSeverity.ERROR,
                        )
                    )
