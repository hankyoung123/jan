import argparse
import os
import socket
from pathlib import Path

import uvicorn

from story_engine.api.app import create_app
from story_engine.config import EngineSettings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="story-engine")
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve", help="start the local story engine")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=39281)
    serve.add_argument("--port-file", type=Path)
    serve.add_argument(
        "--session-token",
        default=os.environ.get("STORY_ENGINE_SESSION_TOKEN"),
    )
    return parser


def _bind_server_socket(
    settings: EngineSettings,
    port_file: Path | None,
) -> socket.socket:
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((settings.host, settings.port))
        server_socket.listen(2048)
        actual_port = server_socket.getsockname()[1]
        if port_file is not None:
            port_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = port_file.with_name(f".{port_file.name}.{os.getpid()}.tmp")
            temporary.write_text(f"{actual_port}\n", encoding="ascii")
            os.replace(temporary, port_file)
        return server_socket
    except BaseException:
        server_socket.close()
        raise


def _serve(settings: EngineSettings, port_file: Path | None) -> None:
    server_socket = _bind_server_socket(settings, port_file)
    try:
        config = uvicorn.Config(
            create_app(settings),
            host=settings.host,
            port=settings.port,
        )
        uvicorn.Server(config).run(sockets=[server_socket])
    finally:
        server_socket.close()
        if port_file is not None:
            port_file.unlink(missing_ok=True)


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
    _serve(settings, args.port_file)
