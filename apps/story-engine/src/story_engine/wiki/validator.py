import re
from pathlib import PurePosixPath

from story_engine.domain.wiki import WikiPatch, WikiPatchOperation, WikiSource

_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_INFERENCE_MARKERS = ("belief", "suspected", "uncertain")


class WikiValidator:
    def __init__(self, *, max_page_chars: int = 65_536) -> None:
        self.max_page_chars = max_page_chars

    def validate(
        self,
        patches: tuple[WikiPatch, ...],
        *,
        branch_id: str,
        sources: tuple[WikiSource, ...],
        subject_id: str | None,
        existing_paths: set[str],
    ) -> None:
        available = {source.source_id: source for source in sources}
        created = set(existing_paths)
        for patch in patches:
            relative = PurePosixPath(patch.path)
            invalid = (
                relative.is_absolute()
                or ".." in relative.parts
                or relative.suffix != ".md"
                or relative.name in {"index.md", "log.md", "SCHEMA.md"}
            )
            if invalid:
                raise ValueError("Wiki patch attempts to modify a store-owned path")
            expected_root = (
                ("world",)
                if subject_id is None
                else ("characters", subject_id)
            )
            if relative.parts[: len(expected_root)] != expected_root:
                raise ValueError("Wiki patch crosses its knowledge boundary")
            if patch.operation == WikiPatchOperation.CREATE:
                if patch.path in created:
                    raise ValueError("Wiki create patch targets an existing page")
                created.add(patch.path)
            elif patch.path not in created:
                raise ValueError("Wiki patch targets a missing page")
            if len(patch.content) > self.max_page_chars:
                raise ValueError("Wiki patch exceeds the page length limit")
            unknown = set(patch.source_ids) - set(available)
            if unknown:
                raise ValueError(f"Wiki patch cites unknown sources: {sorted(unknown)}")
            if any(available[item].branch_id != branch_id for item in patch.source_ids):
                raise ValueError("Wiki patch cites another branch")
            if subject_id is not None and any(
                available[item].subject_id not in {None, subject_id}
                for item in patch.source_ids
            ):
                raise ValueError("Wiki patch leaks another character's knowledge")
            lowered = patch.content.casefold()
            if "maybe" in lowered and not any(
                marker in lowered for marker in _INFERENCE_MARKERS
            ):
                raise ValueError("Wiki inference must be explicitly labelled")
            for link in _LINK.findall(patch.content):
                if "://" in link or link.startswith("/"):
                    raise ValueError("Wiki patches may use only local Markdown links")

