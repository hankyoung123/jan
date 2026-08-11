from pathlib import Path

from story_engine.submission.service import SubmissionService, fog_harbor_submission
from story_engine.wiki.reader import WikiSourceReader


def test_character_sources_include_public_project_seed(
    tmp_path: Path,
) -> None:
    SubmissionService(tmp_path).finalize(fog_harbor_submission())
    reader = WikiSourceReader(tmp_path / "fog-harbor", "main")

    sources = reader.character_sources(
        "chen-mo",
        records=(),
    )

    ids = {source.source_id: source for source in sources}
    assert "project:fog-harbor" in ids
    assert ids["project:fog-harbor"].kind.value == "project"
    assert ids["project:fog-harbor"].subject_id is None
    assert "profile:chen-mo" in ids
    assert "profile:lin-lan" not in ids
