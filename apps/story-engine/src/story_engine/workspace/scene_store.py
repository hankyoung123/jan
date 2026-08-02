from pathlib import Path

from story_engine.manuscript.models import (
    EventAmendmentCandidate,
    Scene,
    SceneDraft,
)
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import SceneDocument, load_document


class SceneStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def list_scenes(self) -> tuple[Scene, ...]:
        scenes: list[Scene] = []
        for path in sorted((self.root / "scenes").glob("*.md")):
            document, body = load_document(path, SceneDocument)
            scenes.append(document.to_domain(body))
        return tuple(scenes)

    def load(self, scene_id: str) -> Scene:
        for scene in self.list_scenes():
            if scene.id == scene_id:
                return scene
        raise FileNotFoundError(scene_id)

    def next_identifier(self) -> tuple[str, int]:
        sequences = [scene.sequence for scene in self.list_scenes()]
        draft_directory = self.root / ".story-engine/scenes"
        for path in draft_directory.glob("scene-*.json"):
            suffix = path.stem.removeprefix("scene-")
            if suffix.isdigit():
                sequences.append(int(suffix))
        sequence = max(sequences, default=0) + 1
        return f"scene-{sequence:06d}", sequence

    @staticmethod
    def relative_path(scene: Scene) -> str:
        return f"scenes/{scene.sequence:06d}.md"


class SceneDraftStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, draft: SceneDraft, *, overwrite: bool = True) -> Path:
        path = self.root / ".story-engine/scenes" / f"{draft.id}.json"
        atomic_write_text(
            path,
            f"{draft.model_dump_json(indent=2)}\n",
            overwrite=overwrite,
        )
        return path

    def load(self, scene_id: str) -> SceneDraft:
        path = self.root / ".story-engine/scenes" / f"{scene_id}.json"
        return SceneDraft.model_validate_json(path.read_text(encoding="utf-8"))

    def list_drafts(self) -> tuple[SceneDraft, ...]:
        drafts = []
        for path in sorted((self.root / ".story-engine/scenes").glob("*.json")):
            drafts.append(
                SceneDraft.model_validate_json(path.read_text(encoding="utf-8"))
            )
        return tuple(drafts)


class AmendmentStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(
        self,
        amendment: EventAmendmentCandidate,
        *,
        overwrite: bool = True,
    ) -> Path:
        path = self.root / ".story-engine/amendments" / f"{amendment.id}.json"
        atomic_write_text(
            path,
            f"{amendment.model_dump_json(indent=2)}\n",
            overwrite=overwrite,
        )
        return path

    def load(self, amendment_id: str) -> EventAmendmentCandidate:
        path = self.root / ".story-engine/amendments" / f"{amendment_id}.json"
        return EventAmendmentCandidate.model_validate_json(
            path.read_text(encoding="utf-8")
        )
