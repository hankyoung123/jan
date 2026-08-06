from pathlib import Path


def test_story_runtime_has_no_prohibited_legacy_architecture_symbols() -> None:
    repository = Path(__file__).resolve().parents[3]
    roots = (
        repository / "apps/story-engine/src/story_engine",
        repository / "web-app/src/features/story",
    )
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix in {".py", ".ts", ".tsx"}
        and ".test." not in path.name
        and "__tests__" not in path.parts
    )
    prohibited = (
        "RagService",
        'task_type="embedding"',
        "tauri_plugin_llamacpp",
        "tauri_plugin_mlx",
        "world_projection_mode",
        "world-bible.json",
        "runtime/logs/",
    )

    assert {marker for marker in prohibited if marker in production} == set()
