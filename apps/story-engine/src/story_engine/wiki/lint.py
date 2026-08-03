import re
from pathlib import Path, PurePosixPath

from story_engine.domain.wiki import (
    WikiLintIssue,
    WikiLintResult,
    WikiLintSeverity,
)
from story_engine.wiki.store import WikiStore

_LINK = re.compile(r"\[[^\]]+\]\(([^)]+\.md)(?:#[^)]*)?\)")


class WikiLinter:
    def __init__(
        self,
        root: Path,
        branch_id: str,
        *,
        max_page_chars: int = 65_536,
    ) -> None:
        self.store = WikiStore(root, branch_id)
        self.branch_id = branch_id
        self.max_page_chars = max_page_chars

    def run(self) -> WikiLintResult:
        pages = self.store.list_pages()
        paths = {page.path for page in pages}
        linked: set[str] = set()
        issues: list[WikiLintIssue] = []
        for page in pages:
            if len(page.content) > self.max_page_chars:
                issues.append(
                    WikiLintIssue(
                        code="page-too-long",
                        message="Wiki page exceeds the bounded context page limit",
                        path=page.path,
                        severity=WikiLintSeverity.ERROR,
                    )
                )
            if not page.source_ids and page.updated_at_step > 0:
                issues.append(
                    WikiLintIssue(
                        code="missing-sources",
                        message="Maintained Wiki page has no source IDs",
                        path=page.path,
                        severity=WikiLintSeverity.ERROR,
                    )
                )
            for target in _LINK.findall(page.content):
                resolved = str(PurePosixPath(PurePosixPath(page.path).parent, target))
                linked.add(resolved)
                if resolved not in paths:
                    issues.append(
                        WikiLintIssue(
                            code="missing-link",
                            message=f"Wiki link points to missing page {target}",
                            path=page.path,
                            severity=WikiLintSeverity.ERROR,
                        )
                    )
        for page in pages:
            if page.path not in linked and page.path not in {
                "world/state.md",
                "world/rules.md",
                "world/threads.md",
            } and not page.path.endswith("/self.md"):
                issues.append(
                    WikiLintIssue(
                        code="orphan-page",
                        message="Wiki page is reachable only from the branch index",
                        path=page.path,
                        severity=WikiLintSeverity.WARNING,
                    )
                )
        return WikiLintResult(
            branch_id=self.branch_id,
            passed=not any(
                issue.severity == WikiLintSeverity.ERROR for issue in issues
            ),
            issues=tuple(issues),
        )
