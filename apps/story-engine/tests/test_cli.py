import socket
from pathlib import Path

from story_engine.cli import _bind_server_socket
from story_engine.config import EngineSettings


def test_sidecar_binds_ephemeral_loopback_port_and_announces_it(
    tmp_path: Path,
) -> None:
    port_file = tmp_path / "runtime" / "sidecar.port"

    server_socket = _bind_server_socket(
        EngineSettings(host="127.0.0.1", port=0),
        port_file,
    )
    try:
        host, port = server_socket.getsockname()

        assert host == "127.0.0.1"
        assert port > 0
        assert port_file.read_text(encoding="ascii") == f"{port}\n"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as competitor:
            competitor.settimeout(0.1)
            assert competitor.connect_ex((host, port)) == 0
    finally:
        server_socket.close()
