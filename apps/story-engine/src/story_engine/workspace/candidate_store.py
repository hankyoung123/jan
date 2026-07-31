from pathlib import Path

from story_engine.domain.models import TurnCandidate
from story_engine.workspace.atomic import atomic_write_text


class CandidateStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, candidate: TurnCandidate, *, overwrite: bool = True) -> Path:
        path = self.root / ".story-engine/turns" / f"{candidate.id}.json"
        atomic_write_text(
            path,
            f"{candidate.model_dump_json(indent=2)}\n",
            overwrite=overwrite,
        )
        return path

    def load(self, candidate_id: str) -> TurnCandidate:
        path = self.root / ".story-engine/turns" / f"{candidate_id}.json"
        return TurnCandidate.model_validate_json(path.read_text(encoding="utf-8"))

    def next_identifier(self) -> str:
        directory = self.root / ".story-engine/turns"
        sequences = []
        for path in directory.glob("turn-*.json"):
            suffix = path.stem.removeprefix("turn-")
            if suffix.isdigit():
                sequences.append(int(suffix))
        sequence = max(sequences, default=0) + 1
        return f"turn-{sequence:06d}"
