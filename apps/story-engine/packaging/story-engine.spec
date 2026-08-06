from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


repository_root = Path(SPECPATH).parents[2]
source_root = repository_root / "apps" / "story-engine" / "src"
entrypoint = Path(SPECPATH) / "sidecar_entry.py"

concordia_datas = collect_data_files("concordia")
hidden_imports = sorted(
    set(
        collect_submodules("story_engine") + collect_submodules("uvicorn")
    )
)

analysis = Analysis(
    [str(entrypoint)],
    pathex=[str(source_root)],
    binaries=[],
    datas=concordia_datas,
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "mypy", "ruff"],
    noarchive=False,
)
python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="story-engine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="story-engine",
)
