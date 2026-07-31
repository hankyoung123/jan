import argparse
import json
from pathlib import Path
from typing import Any

from story_engine.api.app import create_app
from story_engine.config import EngineSettings
from story_engine.workspace.atomic import atomic_write_text


def build_openapi_schema() -> dict[str, Any]:
    app = create_app(
        EngineSettings(
            session_token="contract-generation-token",
            projects_root=Path("/nonexistent-contract-projects"),
        )
    )
    return app.openapi()


def export_openapi(output: Path) -> None:
    content = json.dumps(
        build_openapi_schema(),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    atomic_write_text(output, f"{content}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the story engine OpenAPI")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    export_openapi(arguments.output)


if __name__ == "__main__":
    main()

