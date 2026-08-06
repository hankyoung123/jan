import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def _default_binary() -> Path:
    name = "story-engine.exe" if os.name == "nt" else "story-engine"
    return Path("src-tauri/resources/story-engine") / name


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _get_json(url: str, *, token: str | None = None) -> dict[str, object]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=1) as response:
        payload: object = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise TypeError(f"Expected a JSON object from {url}")
    return {str(key): value for key, value in payload.items()}


def _wait_until_ready(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Sidecar exited before health check: {process.returncode}"
            )
        try:
            health = _get_json(f"{base_url}/health")
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.1)
            continue
        if health.get("status") == "ok" and health.get("service") == "story-engine":
            return
        time.sleep(0.1)
    raise TimeoutError("Sidecar health check timed out")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("binary", nargs="?", type=Path, default=_default_binary())
    args = parser.parse_args()
    binary = args.binary.resolve()
    if not binary.is_file():
        raise FileNotFoundError(f"Sidecar binary not found: {binary}")

    port = _available_port()
    token = "sidecar-smoke-session-token"
    environment = os.environ.copy()
    environment["STORY_ENGINE_SESSION_TOKEN"] = token
    with tempfile.TemporaryDirectory(prefix="story-engine-sidecar-") as directory:
        process = subprocess.Popen(
            [str(binary), "serve", "--host", "127.0.0.1", "--port", str(port)],
            cwd=directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            _wait_until_ready(base_url, process)
            status = _get_json(f"{base_url}/api/status", token=token)
            if status != {"status": "ready"}:
                raise RuntimeError(f"Unexpected authenticated status: {status}")
        except BaseException:
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            sys.stderr.write(stdout)
            sys.stderr.write(stderr)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    print(f"Sidecar smoke passed: {binary}")


if __name__ == "__main__":
    main()
