#!/usr/bin/env python
"""Real-provider closed-loop smoke for the Story Engine (goal 6.3).

Run with valid bridge credentials:

    STORY_ENGINE_MODEL_BASE_URL=http://127.0.0.1:PORT/v1 \
    STORY_ENGINE_MODEL_API_KEY=... \
    STORY_ENGINE_MODEL_REF=deepseek/deepseek-v4-flash \
    uv run --project apps/story-engine python scripts/provider_e2e_check.py

The script finalizes a submission, starts a 3-step simulation through the real
provider bridge, and prints per-stage token/duration/status metrics from the
durable trace. It never prints the API key.
"""

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

TOKEN = "provider-e2e-token"
PROJECT_ID = "fog-harbor"
BRANCH_ID = "main"


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _available_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: object | None = None,
    timeout: float = 180,
) -> tuple[int, object]:
    headers = {"Authorization": f"Bearer {TOKEN}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=data,
        headers=headers,
        method=method,
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {"detail": raw}


def _wait_until_ready(base_url: str, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"sidecar exited: {process.returncode}")
        try:
            status, health = _request(base_url, "/health", timeout=2)
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            time.sleep(0.2)
            continue
        if (
            status == 200
            and isinstance(health, dict)
            and health.get("status") == "ok"
        ):
            return
        time.sleep(0.2)
    raise TimeoutError("sidecar health check timed out")


def _stage_stats(
    trace: object,
    *,
    session_id: str | None = None,
) -> list[dict[str, object]]:
    stats: list[dict[str, object]] = []
    if not isinstance(trace, list):
        return stats
    for record in trace:
        if not isinstance(record, dict):
            continue
        trace_payload = record.get("trace")
        if not isinstance(trace_payload, dict):
            continue
        if session_id is not None and trace_payload.get("session_id") != session_id:
            continue
        calls = {
            call.get("call_id"): call
            for call in trace_payload.get("model_calls", [])
            if isinstance(call, dict) and isinstance(call.get("call_id"), str)
        }
        for stage in trace_payload.get("stages", []):
            if not isinstance(stage, dict):
                continue
            stage_calls = [
                calls[call_id]
                for call_id in stage.get("model_call_ids", [])
                if isinstance(call_id, str) and call_id in calls
            ]
            stats.append(
                {
                    "step": trace_payload.get("step"),
                    "stage": stage.get("stage_type"),
                    "status": stage.get("status"),
                    "error_code": stage.get("error_code"),
                    "duration_ms": stage.get("duration_ms"),
                    "prompt_tokens": sum(
                        int(call.get("prompt_tokens") or 0) for call in stage_calls
                    ),
                    "completion_tokens": sum(
                        int(call.get("completion_tokens") or 0)
                        for call in stage_calls
                    ),
                    "reasoning_tokens": sum(
                        int(call.get("reasoning_tokens") or 0)
                        for call in stage_calls
                    ),
                    "finish_reasons": sorted(
                        {
                            str(call["finish_reason"])
                            for call in stage_calls
                            if call.get("finish_reason")
                        }
                    ),
                    "max_retry_count": max(
                        (int(call.get("retry_count") or 0) for call in stage_calls),
                        default=0,
                    ),
                    "profiles": sorted(
                        {
                            profile
                            for call in stage_calls
                            if isinstance(call.get("profile_id"), str)
                            for profile in [call["profile_id"]]
                        }
                    ),
                }
            )
    return stats


def main() -> None:
    base_url_value = _require_env("STORY_ENGINE_MODEL_BASE_URL")
    api_key = _require_env("STORY_ENGINE_MODEL_API_KEY")
    model_ref = os.environ.get("STORY_ENGINE_MODEL_REF", "deepseek/deepseek-v4-flash")

    from story_engine.models.contracts import ModelProfile
    from story_engine.models.registry import ProfileRegistry
    from story_engine.submission.service import SubmissionService, fog_harbor_submission

    port = _available_port()
    with tempfile.TemporaryDirectory(prefix="story-engine-provider-e2e-") as directory:
        root = Path(directory)
        (root / "config").mkdir()
        registry = ProfileRegistry(root / "config" / "model-registry.json")
        for profile_id, task_type, max_tokens in (
            ("actor", "actor", 4096),
            ("game-master", "game_master", 8192),
            ("wiki-maintenance", "wiki_maintenance", 8192),
            ("editor", "editor", 4096),
            ("writer", "writer", 8192),
        ):
            registry.upsert_profile(
                ModelProfile(
                    id=profile_id,
                    task_type=task_type,  # type: ignore[arg-type]
                    model_ref=model_ref,
                    max_output_tokens=max_tokens,
                    timeout_seconds=120,
                    reasoning_effort="low",
                )
            )
        SubmissionService(root / "projects").finalize(fog_harbor_submission())

        environment = os.environ.copy()
        environment["STORY_ENGINE_SESSION_TOKEN"] = TOKEN
        environment["STORY_ENGINE_MODEL_BASE_URL"] = base_url_value
        environment["STORY_ENGINE_MODEL_API_KEY"] = api_key
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from story_engine.cli import main; main()",
                "serve",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            _wait_until_ready(base_url, process)
            max_attempts = max(
                1, int(os.environ.get("STORY_ENGINE_E2E_ATTEMPTS", "3"))
            )
            summary: dict[str, object] | None = None
            for attempt in range(1, max_attempts + 1):
                status, started = _request(
                    base_url,
                    f"/projects/{PROJECT_ID}/simulations",
                    method="POST",
                    body={
                        "premise_text": "灯塔突然熄灭，调查员进入灯塔。",
                        "actor_ids": ["chen-mo", "lin-lan"],
                        "content_locale": "zh-CN",
                        "control": {
                            "mode": "step",
                            "max_steps": 3,
                            "max_scenes": 2,
                            "max_total_tokens": 100_000,
                            "max_runtime_seconds": 900,
                            "max_consecutive_model_failures": 2,
                            "checkpoint_every_steps": 1,
                        },
                        "output": {
                            "manuscript_mode": "manual",
                            "wiki_mode": "after_scene",
                        },
                    },
                )
                if status != 201 or not isinstance(started, dict):
                    raise RuntimeError(f"simulation start failed: {started}")
                session_id = started["session_id"]

                steps: list[dict[str, object]] = []
                try:
                    for _ in range(3):
                        step_status, step = _request(
                            base_url,
                            f"/projects/{PROJECT_ID}/simulations/{session_id}/step",
                            method="POST",
                            timeout=600,
                        )
                        if (
                            step_status == 409
                            and isinstance(step, dict)
                            and "maintenance" in str(step.get("detail", "")).lower()
                        ):
                            maintenance_attempts = 0
                            while maintenance_attempts < 2:
                                maint_status, maint = _request(
                                    base_url,
                                    f"/projects/{PROJECT_ID}/simulations/"
                                    f"{session_id}/maintenance/retry",
                                    method="POST",
                                    timeout=600,
                                )
                                if maint_status == 200 and isinstance(maint, dict):
                                    break
                                maintenance_attempts += 1
                                if maintenance_attempts >= 2:
                                    raise RuntimeError(
                                        f"maintenance retry failed: {maint}"
                                    )
                            step_status, step = _request(
                                base_url,
                                f"/projects/{PROJECT_ID}/simulations/"
                                f"{session_id}/step",
                                method="POST",
                                timeout=600,
                            )
                        if step_status != 200 or not isinstance(step, dict):
                            raise RuntimeError(f"step failed: {step}")
                        steps.append(
                            {
                                "step": step.get("step"),
                                "status": step.get("status"),
                                "boundary": step.get("boundary"),
                            }
                        )
                except Exception as error:
                    if attempt == max_attempts:
                        raise
                    print(
                        f"attempt {attempt} failed "
                        f"({type(error).__name__}: {error}); "
                        "starting a fresh session",
                        file=sys.stderr,
                    )
                    _request(
                        base_url,
                        f"/projects/{PROJECT_ID}/simulations/{session_id}/terminate",
                        method="POST",
                        body={"reason_text": "provider e2e restart"},
                    )
                    continue

                trace_status, trace = _request(
                    base_url,
                    f"/projects/{PROJECT_ID}/branches/{BRANCH_ID}/simulation-trace"
                    "?after_step=-1",
                )
                if trace_status != 200:
                    raise RuntimeError(f"trace failed: {trace}")
                wiki_status, wiki = _request(
                    base_url,
                    f"/projects/{PROJECT_ID}/branches/{BRANCH_ID}/wiki",
                )
                summary = {
                    "model_ref": model_ref,
                    "attempt": attempt,
                    "session_id": session_id,
                    "steps": steps,
                    "wiki_status": wiki_status,
                    "wiki_updated_at_step": (
                        wiki.get("updated_at_step")
                        if isinstance(wiki, dict)
                        else None
                    ),
                    "stages": _stage_stats(trace, session_id=session_id),
                }
                break
            if summary is None:
                raise RuntimeError("provider closed loop did not complete")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        except BaseException:
            process.terminate()
            stdout, stderr = process.communicate(timeout=10)
            sys.stderr.write(stdout)
            sys.stderr.write(stderr)
            raise
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=10)


if __name__ == "__main__":
    main()
