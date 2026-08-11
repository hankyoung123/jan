"""Deterministic Replay runtime soak with memory restore and Recall."""

import argparse
import json
import time
from threading import Event

from story_engine.concordia_runtime.replay_scenario import replay_runtime_factory
from story_engine.domain.action import ActionOutputType, ActionSpec
from story_engine.domain.recipe import PerceptionFrame
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    TurnSessionRequest,
    TurnSessionStatus,
)
from story_engine.simulation.engine import StoryTurnEngine


def run(turns: int) -> dict[str, object]:
    request = TurnSessionRequest(
        project_id="benchmark",
        branch_id="main",
        premise_text="Run a deterministic archive investigation.",
        actor_ids=("actor-a",),
        content_locale="en-US",
        control=ControlPolicy(mode=ControlMode.AUTONOMOUS, max_steps=turns),
    )
    factory = replay_runtime_factory()
    engine = StoryTurnEngine(factory)
    created = engine.create_session(request)
    started = time.perf_counter()
    snapshot = created
    while snapshot.status != TurnSessionStatus.TERMINATED:
        engine.advance_one_step(created.session_id, cancellation=Event())
        snapshot = engine.get(created.session_id)
    elapsed = time.perf_counter() - started
    memory_records = engine.pending_memory_records(created.session_id)

    restored = factory(created.session_id, request)
    restored.restore_states(
        actor_states=snapshot.actor_states,
        game_master_states=snapshot.game_master_states,
    )
    restored.replay_memory_records(memory_records)
    actor = restored.actors[0]
    actor_records_before_recall = actor.memory.records()
    actor.observe(
        PerceptionFrame(
            frame_id="observation:benchmark:recall",
            session_id=created.session_id,
            branch_id="main",
            actor_id="actor-a",
            step=turns,
            content_locale="en-US",
            observation_text="Resolved event 0 becomes relevant again.",
            tags=("action",),
        )
    )
    actor.act(
        ActionSpec(
            spec_id="action:benchmark:recall",
            output_type=ActionOutputType.FREE,
            call_to_action="Respond to resolved event 0.",
            tag="action",
            content_locale="en-US",
        )
    )
    prompt_parts = actor.get_last_log()["__act__"]["Prompt"]
    if not isinstance(prompt_parts, list):
        raise RuntimeError("Replay actor prompt log is unavailable")
    prompt = "\n".join(str(part) for part in prompt_parts)
    relevant = prompt.split("Relevant Recall:", 1)[1].split(
        "Current Perception:", 1
    )[0]
    result = {
        "steps": snapshot.current_step,
        "seconds": round(elapsed, 4),
        "steps_per_second": round(snapshot.current_step / elapsed, 2),
        "actor_memory_records": len(actor_records_before_recall),
        "durable_memory_records": len(memory_records),
        "restored_memory_records": (
            len(actor.memory.records())
            + len(restored.game_master.memory.records())
            - 1
        ),
        "recall_found": "Resolved event 0" in relevant,
        "prompt_chars": len(prompt),
        "prompt_bounded": len(prompt) < 60_000,
        "state_hash": snapshot.state_hash,
    }
    if result["restored_memory_records"] != result["durable_memory_records"]:
        raise RuntimeError("Replay memory restore count mismatch")
    if not result["recall_found"] or not result["prompt_bounded"]:
        raise RuntimeError("Replay Recall or prompt bound invariant failed")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turns", type=int, default=300)
    args = parser.parse_args()
    if args.turns < 20:
        parser.error("turn count must be at least 20")
    print(json.dumps(run(args.turns), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
