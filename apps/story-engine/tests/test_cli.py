import json
import socket
import subprocess
import sys
import time
import urllib.request
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


def test_managed_shutdown_exits_the_sidecar_cleanly(tmp_path: Path) -> None:
    port_file = tmp_path / "runtime" / "sidecar.port"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from story_engine.cli import main; main()",
            "serve",
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--port-file",
            str(port_file),
            "--session-token",
            "shutdown-token",
        ],
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not port_file.exists() and process.poll() is None:
            if time.monotonic() >= deadline:
                raise AssertionError("sidecar did not announce its port")
            time.sleep(0.05)
        assert process.poll() is None
        port = int(port_file.read_text(encoding="ascii"))
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/internal/shutdown",
            method="POST",
            headers={"Authorization": "Bearer shutdown-token"},
        )

        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.load(response)
            assert response.status == 202
        assert payload == {"status": "shutting_down"}
        assert process.wait(timeout=10) == 0
        assert not port_file.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
