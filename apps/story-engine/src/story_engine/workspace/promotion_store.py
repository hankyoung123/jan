import re
from pathlib import Path

from story_engine.domain.models import PromotionCandidate
from story_engine.workspace.atomic import atomic_write_text

_CHARACTER_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class PromotionProposalStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, character_id: str) -> Path:
        if not _CHARACTER_ID.fullmatch(character_id):
            raise ValueError("promotion character ID must be path-safe")
        return self.root / ".story-engine/reviews" / f"promotion-{character_id}.json"

    def save(self, candidate: PromotionCandidate) -> Path:
        path = self._path(candidate.character_id)
        atomic_write_text(path, f"{candidate.model_dump_json(indent=2)}\n")
        return path

    def load(self, character_id: str) -> PromotionCandidate:
        return PromotionCandidate.model_validate_json(
            self._path(character_id).read_text(encoding="utf-8")
        )

    def delete(self, character_id: str) -> None:
        self._path(character_id).unlink(missing_ok=True)
