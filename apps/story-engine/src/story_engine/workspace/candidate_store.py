from pathlib import Path

from story_engine.domain.models import TurnCandidate
from story_engine.workspace.atomic import atomic_write_text


class CandidateStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, candidate: TurnCandidate) -> Path:
        path = self.root / ".story-engine/turns" / f"{candidate.id}.json"
        atomic_write_text(path, f"{candidate.model_dump_json(indent=2)}\n")
        return path

    def load(self, candidate_id: str) -> TurnCandidate:
        path = self.root / ".story-engine/turns" / f"{candidate_id}.json"
        return TurnCandidate.model_validate_json(path.read_text(encoding="utf-8"))

