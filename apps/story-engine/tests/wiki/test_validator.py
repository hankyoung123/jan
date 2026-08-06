from pathlib import Path

import pytest

from story_engine.domain.wiki import (
    WikiPatch,
    WikiPatchOperation,
    WikiSource,
    WikiSourceKind,
)
from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.wiki.store import WikiStore
from story_engine.wiki.validator import WikiValidator


def _store(tmp_path: Path) -> WikiStore:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    return WikiStore(tmp_path / "fog-harbor", "main")


def test_character_patch_cannot_cross_subject_or_cite_private_foreign_source(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    sources = (
        WikiSource(
            source_id="observation:chen:1",
            kind=WikiSourceKind.OBSERVATION,
            branch_id="main",
            subject_id="chen-mo",
            step=1,
            content="Chen sees a brass key.",
        ),
        WikiSource(
            source_id="observation:lin:1",
            kind=WikiSourceKind.OBSERVATION,
            branch_id="main",
            subject_id="lin-lan",
            step=1,
            content="Lin hides the roster.",
        ),
    )
    validator = WikiValidator()

    with pytest.raises(ValueError, match="crosses its knowledge boundary"):
        validator.validate(
            (
                WikiPatch(
                    path="characters/lin-lan/beliefs.md",
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content="- stolen knowledge",
                    source_ids=("observation:chen:1",),
                ),
            ),
            branch_id="main",
            sources=sources,
            subject_id="chen-mo",
            existing_paths={page.path for page in store.list_pages()},
        )

    with pytest.raises(ValueError, match="leaks another character"):
        validator.validate(
            (
                WikiPatch(
                    path="characters/chen-mo/beliefs.md",
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content="- stolen knowledge",
                    source_ids=("observation:lin:1",),
                ),
            ),
            branch_id="main",
            sources=sources,
            subject_id="chen-mo",
            existing_paths={page.path for page in store.list_pages()},
        )


def test_patch_requires_existing_source_and_labels_uncertainty(tmp_path: Path) -> None:
    store = _store(tmp_path)
    source = WikiSource(
        source_id="event:main:1",
        kind=WikiSourceKind.EVENT,
        branch_id="main",
        step=1,
        content="The light goes dark.",
    )
    validator = WikiValidator()
    existing = {page.path for page in store.list_pages()}

    with pytest.raises(ValueError, match="unknown sources"):
        validator.validate(
            (
                WikiPatch(
                    path="world/state.md",
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content="The harbor closes.",
                    source_ids=("event:missing",),
                ),
            ),
            branch_id="main",
            sources=(source,),
            subject_id=None,
            existing_paths=existing,
        )
    with pytest.raises(ValueError, match="explicitly labelled"):
        validator.validate(
            (
                WikiPatch(
                    path="world/state.md",
                    operation=WikiPatchOperation.APPEND_HISTORY,
                    content="Maybe the keeper fled.",
                    source_ids=(source.source_id,),
                ),
            ),
            branch_id="main",
            sources=(source,),
            subject_id=None,
            existing_paths=existing,
        )

