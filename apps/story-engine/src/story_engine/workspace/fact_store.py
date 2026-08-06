from pathlib import Path
from urllib.parse import quote

from story_engine.domain.models import Fact
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import FactDocument, load_document, render_fact
from story_engine.workspace.lock import ProjectLock


class FactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def relative_path(fact_id: str) -> str:
        return f"facts/{quote(fact_id, safe='')}.md"

    def save(self, fact: Fact, *, overwrite: bool = False) -> Path:
        with ProjectLock(self.root):
            path = self.root / self.relative_path(fact.id)
            atomic_write_text(path, render_fact(fact), overwrite=overwrite)
            return path

    def load(self, fact_id: str) -> Fact:
        path = self.root / self.relative_path(fact_id)
        document, _ = load_document(path, FactDocument)
        return document.to_domain()

    def list_facts(self) -> tuple[Fact, ...]:
        facts: list[Fact] = []
        for path in sorted((self.root / "facts").glob("*.md")):
            document, _ = load_document(path, FactDocument)
            facts.append(document.to_domain())
        return tuple(facts)
