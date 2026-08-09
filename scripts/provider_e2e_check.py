#!/usr/bin/env python
"""Real-provider closed-loop smoke for the Story Engine (goal 6.3).

Run with valid bridge credentials:

    STORY_ENGINE_MODEL_BASE_URL=http://127.0.0.1:PORT/v1 \
    STORY_ENGINE_MODEL_API_KEY=... \
    STORY_ENGINE_MODEL_REF=deepseek/deepseek-v4-flash \
    uv run --project apps/story-engine python scripts/provider_e2e_check.py

The script finalizes a submission and runs through the real provider bridge. By
default it executes a 3-step autonomous smoke and prints durable trace metrics.
Set ``STORY_ENGINE_E2E_INTERACTIVE_TURNS=3`` for the interactive MVP gate; that
mode also verifies command replay and reopening the durable branch head.
It never prints the API key.
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


def _run_interactive(
    base_url: str,
    *,
    turns: int,
    model_ref: str,
) -> dict[str, object]:
    project_id = "last-ferry-before"
    status, opened = _request(
        base_url,
        f"/projects/{project_id}/simulation/session",
        timeout=600,
    )
    if status != 200 or not isinstance(opened, dict):
        raise RuntimeError(f"interactive session start failed: {opened}")

    initial_checkpoint = opened.get("checkpoint_id")
    checkpoints: list[str] = []
    visible_event_count = 0
    idempotency_verified = False
    failed_turn_attempts = 0
    max_turn_attempts = max(
        1, int(os.environ.get("STORY_ENGINE_E2E_TURN_ATTEMPTS", "3"))
    )
    intentions = (
        "我环顾大厅，确认每个人的位置和正在做的事。",
        "我向林澈询问那条约我来旅馆的消息是谁发的。",
        "我观察张野的行李和他准备离开的迹象。",
        "我询问店主二楼锁房今天是否有人进去过。",
        "我用手机联系警员陈凯，请他核查港口交接记录。",
        "我核对目前掌握的时间线，并要求相关人解释矛盾。",
    )
    for index in range(turns):
        turn: dict[str, object] | None = None
        command_id = f"interactive:provider-e2e-{index + 1:02d}"
        for attempt in range(1, max_turn_attempts + 1):
            turn_status, candidate = _request(
                base_url,
                f"/projects/{project_id}/simulation/turn",
                method="POST",
                body={
                    "text": intentions[index % len(intentions)],
                    "command_id": command_id,
                },
                timeout=600,
            )
            if turn_status == 200 and isinstance(candidate, dict):
                turn = candidate
                break
            failed_turn_attempts += 1
            detail = (
                candidate.get("detail")
                if isinstance(candidate, dict)
                else "non-object response"
            )
            print(
                f"interactive turn {index + 1}/{turns} attempt {attempt} "
                f"failed: HTTP {turn_status} ({detail})",
                file=sys.stderr,
                flush=True,
            )
        if turn is None:
            raise RuntimeError(
                f"interactive turn {index + 1} failed after "
                f"{max_turn_attempts} attempts"
            )
        checkpoint_id = turn.get("checkpoint_id")
        if not isinstance(checkpoint_id, str):
            raise TypeError(f"interactive turn {index + 1} has no checkpoint")
        checkpoints.append(checkpoint_id)
        visible = turn.get("visible_events")
        if isinstance(visible, list):
            visible_event_count += len(visible)
        print(
            f"interactive turn {index + 1}/{turns}: "
            f"checkpoint={checkpoint_id} visible={len(visible or [])}",
            file=sys.stderr,
            flush=True,
        )

        if index == 0:
            timeline_status, before_replay = _request(
                base_url,
                f"/projects/{project_id}/branches/{BRANCH_ID}/timeline",
            )
            replay_status, replay = _request(
                base_url,
                f"/projects/{project_id}/simulation/turn",
                method="POST",
                body={
                    "text": intentions[0],
                    "command_id": command_id,
                },
                timeout=600,
            )
            after_status, after_replay = _request(
                base_url,
                f"/projects/{project_id}/branches/{BRANCH_ID}/timeline",
            )
            idempotency_verified = (
                timeline_status == 200
                and replay_status == 200
                and after_status == 200
                and isinstance(replay, dict)
                and replay.get("checkpoint_id") == checkpoint_id
                and before_replay == after_replay
            )
            if not idempotency_verified:
                raise RuntimeError("interactive command replay was not idempotent")

    if len(set(checkpoints)) != turns:
        raise RuntimeError("interactive turns did not produce unique checkpoints")
    timeline_status, timeline = _request(
        base_url,
        f"/projects/{project_id}/branches/{BRANCH_ID}/timeline",
    )
    reopened_status, reopened = _request(
        base_url,
        f"/projects/{project_id}/simulation/session",
        timeout=600,
    )
    trace_status, trace = _request(
        base_url,
        f"/projects/{project_id}/branches/{BRANCH_ID}/simulation-trace"
        "?after_step=-1",
    )
    if timeline_status != 200 or not isinstance(timeline, list):
        raise RuntimeError(f"interactive timeline failed: {timeline}")
    if reopened_status != 200 or not isinstance(reopened, dict):
        raise RuntimeError(f"interactive reopen failed: {reopened}")
    if reopened.get("checkpoint_id") != checkpoints[-1]:
        raise RuntimeError("interactive reopen did not restore the branch head")
    if trace_status != 200:
        raise RuntimeError(f"interactive trace failed: {trace}")
    stages = _stage_stats(trace)
    return {
        "model_ref": model_ref,
        "interactive_turns": turns,
        "initial_checkpoint": initial_checkpoint,
        "final_checkpoint": checkpoints[-1],
        "unique_turn_checkpoints": len(set(checkpoints)),
        "timeline_entries": len(timeline),
        "visible_event_count": visible_event_count,
        "failed_turn_attempts": failed_turn_attempts,
        "idempotency_verified": idempotency_verified,
        "reopen_verified": True,
        "world_time": reopened.get("world_time"),
        "model_stages": len(stages),
        "failed_model_stages": sum(
            1 for stage in stages if stage.get("status") != "succeeded"
        ),
        "prompt_tokens": sum(int(stage["prompt_tokens"]) for stage in stages),
        "completion_tokens": sum(
            int(stage["completion_tokens"]) for stage in stages
        ),
    }


def main() -> None:
    base_url_value = _require_env("STORY_ENGINE_MODEL_BASE_URL")
    api_key = _require_env("STORY_ENGINE_MODEL_API_KEY")
    model_ref = os.environ.get("STORY_ENGINE_MODEL_REF", "deepseek/deepseek-v4-flash")

    from story_engine.models.contracts import AgentProfilePatch
    from story_engine.models.registry import ProfileRegistry
    from story_engine.submission.service import (
        SubmissionService,
        fog_harbor_submission,
        last_ferry_before_submission,
    )

    interactive_turns = max(
        0, int(os.environ.get("STORY_ENGINE_E2E_INTERACTIVE_TURNS", "0"))
    )

    port = _available_port()
    with tempfile.TemporaryDirectory(prefix="story-engine-provider-e2e-") as directory:
        root = Path(directory)
        (root / "config").mkdir()
        registry = ProfileRegistry(root / "config" / "model-registry.json")
        for agent_type, max_tokens in (
            ("actor", 4096),
            ("game_master", 8192),
            ("wiki_maintainer", 8192),
            ("editor", 4096),
            ("writer", 8192),
            ("submission_editor", 4096),
        ):
            registry.patch_profile(
                agent_type,  # type: ignore[arg-type]
                AgentProfilePatch(
                    model=model_ref,
                    max_output_tokens=max_tokens,
                    timeout_seconds=120,
                    reasoning_effort="low",
                )
            )
        SubmissionService(root / "projects").finalize(
            last_ferry_before_submission()
            if interactive_turns
            else fog_harbor_submission()
        )

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
            if interactive_turns:
                print(
                    json.dumps(
                        _run_interactive(
                            base_url,
                            turns=interactive_turns,
                            model_ref=model_ref,
                        ),
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return
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
                state_hash = started["state_hash"]

                steps: list[dict[str, object]] = []
                try:
                    for step_number in range(3):
                        step_status, step = _request(
                            base_url,
                            f"/projects/{PROJECT_ID}/simulations/{session_id}/step",
                            method="POST",
                            body={
                                "command_id": f"provider-e2e:{step_number}",
                                "expected_state_hash": state_hash,
                            },
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
                                body={
                                    "command_id": f"provider-e2e:{step_number}",
                                    "expected_state_hash": state_hash,
                                },
                                timeout=600,
                            )
                        if step_status != 200 or not isinstance(step, dict):
                            raise RuntimeError(f"step failed: {step}")
                        snapshot_status, snapshot = _request(
                            base_url,
                            f"/projects/{PROJECT_ID}/simulations/{session_id}",
                        )
                        if snapshot_status != 200 or not isinstance(snapshot, dict):
                            raise RuntimeError(f"snapshot failed: {snapshot}")
                        state_hash = str(snapshot["state_hash"])
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
