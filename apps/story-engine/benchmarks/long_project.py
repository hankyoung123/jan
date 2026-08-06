"""Deterministic 100-step Concordia runtime benchmark."""

import json
import time
from threading import Event

from story_engine.concordia_runtime.replay_scenario import replay_runtime_factory
from story_engine.domain.simulation import (
    ControlMode,
    ControlPolicy,
    TurnSessionRequest,
)
from story_engine.simulation.engine import StoryTurnEngine


def main() -> None:
    engine = StoryTurnEngine(replay_runtime_factory())
    created = engine.create_session(
        TurnSessionRequest(
            project_id="benchmark",
            branch_id="main",
            premise_text="Run a deterministic archive investigation.",
            actor_ids=("actor-a",),
            content_locale="en-US",
            control=ControlPolicy(mode=ControlMode.AUTONOMOUS, max_steps=100),
        )
    )
    started = time.perf_counter()
    snapshot = engine.run(created.session_id, cancellation=Event())
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "steps": snapshot.current_step,
                "seconds": round(elapsed, 4),
                "steps_per_second": round(snapshot.current_step / elapsed, 2),
                "actor_memory_records": snapshot.memory_snapshots[
                    "actor-a"
                ].record_count,
                "gm_memory_records": snapshot.memory_snapshots["gm"].record_count,
                "state_hash": snapshot.state_hash,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
