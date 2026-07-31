import argparse

import uvicorn

from story_engine.api.app import create_app
from story_engine.config import EngineSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="story-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="start the local story engine")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=39281)
    serve.add_argument("--session-token")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command != "serve":
        raise RuntimeError(f"unsupported command: {args.command}")

    if args.session_token is None:
        settings = EngineSettings(host=args.host, port=args.port)
    else:
        settings = EngineSettings(
            session_token=args.session_token,
            host=args.host,
            port=args.port,
        )
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)
