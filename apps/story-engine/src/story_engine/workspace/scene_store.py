import re
from pathlib import Path

from story_engine.manuscript.models import Scene, SceneDraft
from story_engine.workspace.atomic import atomic_write_text
from story_engine.workspace.documents import (
    SceneDocument,
    dump_json_envelope,
    load_document,
    load_json_envelope,
    render_scene,
)

_BRANCH_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{0,127}$")


def _branch_directory(root: Path, branch_id: str) -> Path:
    if not _BRANCH_ID.fullmatch(branch_id):
        raise ValueError("invalid branch ID")
    return root / ".story-engine/manuscript" / branch_id


class SceneStore:
    def __init__(self, root: Path, branch_id: str) -> None:
        self.root = root
        self.branch_id = branch_id
        self.directory = _branch_directory(root, branch_id) / "scenes"

    def list_scenes(self) -> tuple[Scene, ...]:
        scenes: list[Scene] = []
        for path in sorted(self.directory.glob("*.md")):
            document, body = load_document(path, SceneDocument)
            scene = document.to_domain(body)
            if scene.branch_id != self.branch_id:
                raise ValueError("scene belongs to another branch")
            scenes.append(scene)
        return tuple(sorted(scenes, key=lambda item: item.sequence))

    def load(self, scene_id: str) -> Scene:
        for scene in self.list_scenes():
            if scene.id == scene_id:
                return scene
        raise FileNotFoundError(scene_id)

    def save(self, scene: Scene, *, overwrite: bool) -> Path:
        if scene.branch_id != self.branch_id:
            raise ValueError("scene belongs to another branch")
        path = self.directory / f"{scene.id}.md"
        atomic_write_text(path, render_scene(scene), overwrite=overwrite)
        return path

    def next_identifier(self) -> tuple[str, int]:
        sequences = [scene.sequence for scene in self.list_scenes()]
        draft_directory = _branch_directory(self.root, self.branch_id) / "drafts"
        for path in draft_directory.glob("scene-*.md"):
            suffix = path.stem.removeprefix("scene-")
            if suffix.isdigit():
                sequences.append(int(suffix))
        sequence = max(sequences, default=0) + 1
        return f"scene-{sequence:06d}", sequence


class SceneDraftStore:
    def __init__(self, root: Path, branch_id: str) -> None:
        self.root = root
        self.branch_id = branch_id
        self.directory = _branch_directory(root, branch_id) / "drafts"

    def save(self, draft: SceneDraft, *, overwrite: bool = True) -> Path:
        if draft.branch_id != self.branch_id:
            raise ValueError("scene draft belongs to another branch")
        path = self.directory / f"{draft.id}.md"
        atomic_write_text(
            path,
            dump_json_envelope(
                schema="story-engine/scene-draft/v1",
                title=f"Scene Draft {draft.id}",
                metadata={
                    "scene_id": draft.id,
                    "branch_id": draft.branch_id,
                    "sequence": draft.sequence,
                    "status": str(draft.status),
                },
                body=f"# {draft.title}\n\n{draft.body}",
                payload=draft.model_dump(mode="json"),
            ),
            overwrite=overwrite,
        )
        return path

    def load(self, scene_id: str) -> SceneDraft:
        path = self.directory / f"{scene_id}.md"
        draft = SceneDraft.model_validate(
            load_json_envelope(path, schema="story-engine/scene-draft/v1")
        )
        if draft.branch_id != self.branch_id:
            raise ValueError("scene draft belongs to another branch")
        return draft

    def list_drafts(self) -> tuple[SceneDraft, ...]:
        drafts = []
        for path in sorted(self.directory.glob("*.md")):
            draft = SceneDraft.model_validate(
                load_json_envelope(path, schema="story-engine/scene-draft/v1")
            )
            if draft.branch_id != self.branch_id:
                raise ValueError("scene draft belongs to another branch")
            drafts.append(draft)
        return tuple(sorted(drafts, key=lambda item: item.sequence))
