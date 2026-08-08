# World Simulation Completion Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Complete the World Session MVP's shared actor turn loop, durable branch replay, and required acceptance coverage without adding a second simulation system.

**Architecture:** `StorySimulationRuntime` remains the sole Concordia execution kernel. A player action is committed through its existing human intent path, then one GM-selected non-player action is committed through the existing automatic-step path; the API response derives visible events from both durable results. Branch selection rehydrates a checkpoint into a branch-bound session and the session UI projects existing branch/checkpoint records.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, Concordia, Vitest, React, TanStack Router.

---

### Task 1: Ground Every Resolution in Committed World Time

**Files:**
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/prefabs/game_master.py`
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/factory.py`
- Modify: `apps/story-engine/src/story_engine/simulation/runtime.py`
- Test: `apps/story-engine/tests/concordia_runtime/test_resolver.py`

**Step 1: Write failing coverage**

Verify the Game Master receives a current committed world-state summary and a resolution can atomically update `WorldState.current_time` without moving a clock-formatted time backwards.

**Step 2: Add the smallest grounded context path**

Reuse the Game Master's existing constant context component pattern for a current-world-state summary. Set it immediately before every resolver call. Keep state changes inside `ResolutionEnvelope.state_updates`; do not create a clock service.

**Step 3: Make prompt requirements explicit**

Tell the GM to use the current committed time, keep clock-formatted times monotonic, and advance time by a plausible amount where the action consumes time.

**Step 4: Run focused tests**

Run: `uv run --project apps/story-engine --extra dev pytest -q apps/story-engine/tests/concordia_runtime/test_resolver.py apps/story-engine/tests/simulation/test_runtime_lifecycle.py`

### Task 2: Commit an NPC Response After Each Interactive Intent

**Files:**
- Modify: `apps/story-engine/src/story_engine/simulation/runtime.py`
- Modify: `apps/story-engine/src/story_engine/simulation/engine.py`
- Modify: `apps/story-engine/src/story_engine/simulation/command_service.py`
- Modify: `apps/story-engine/src/story_engine/simulation/perception.py`
- Test: `apps/story-engine/tests/api/test_interactive_session.py`
- Test: `apps/story-engine/tests/simulation/test_runtime_lifecycle.py`

**Step 1: Write failing coverage**

Use a controllable runtime to prove a player input is followed by an eligible NPC action, that both results have separate durable commits, and that the player response includes only the visible events from those commits.

**Step 2: Reuse the automatic-step execution path**

Add an internal eligible-actor filter to the existing `execute_step` path. The command service executes one human step followed by one NPC-only automatic step when an active NPC is present; it does not add a new engine or direct world mutation API.

**Step 3: Aggregate only the response projection**

Return a response-only combination of committed `ResolvedEvent`s so `PerceptionBuilder` can render both player and NPC consequences. Persist the individual steps unchanged so `ResolvedEvent` remains committed history.

**Step 4: Run focused tests**

Run: `uv run --project apps/story-engine --extra dev pytest -q apps/story-engine/tests/api/test_interactive_session.py apps/story-engine/tests/simulation/test_runtime_lifecycle.py`

### Task 3: Resume a Forked Checkpoint in Its Own Branch

**Files:**
- Modify: `apps/story-engine/src/story_engine/simulation/persistence.py`
- Modify: `apps/story-engine/src/story_engine/simulation/service.py`
- Modify: `apps/story-engine/src/story_engine/api/routes/simulations.py`
- Modify: `apps/story-engine/src/story_engine/simulation/perception.py`
- Test: `apps/story-engine/tests/api/test_interactive_session.py`
- Test: `apps/story-engine/tests/persistence/test_commit.py`

**Step 1: Write failing coverage**

Create a branch from a checkpoint, reopen it as an interactive session, take a different turn, and assert its head, session branch id, state hash, and visible events diverge without moving `main`.

**Step 2: Bind restored snapshots to the selected branch**

Rehydrate the immutable checkpoint through the existing runtime factory with a fresh session id and a request whose branch id is the target branch. Branch manifests stay authoritative for heads and lineage.

**Step 3: Add minimal timeline projection data**

Expose checkpoint id, world time, and step as an API projection derived from the selected branch's existing checkpoint lineage. It exposes no raw runtime state or private memories.

**Step 4: Run focused tests**

Run: `uv run --project apps/story-engine --extra dev pytest -q apps/story-engine/tests/api/test_interactive_session.py apps/story-engine/tests/api/test_simulations.py apps/story-engine/tests/persistence/test_commit.py`

### Task 4: Add the World Session Timeline and Acceptance Matrix

**Files:**
- Create: `web-app/src/features/story/session/Timeline.tsx`
- Modify: `web-app/src/features/story/session/WorldSessionView.tsx`
- Modify: `web-app/src/features/story/session/WorldSessionView.test.tsx`
- Modify: `apps/story-engine/tests/api/test_interactive_session.py`
- Modify: `docs/architecture.md`
- Modify: `docs/data-contracts.md`

**Step 1: Render only committed checkpoints**

Load the selected branch timeline, show world time and current marker, and offer a clearly labelled fork command on an earlier checkpoint. Do not add action suggestions or a separate save-game store.

**Step 2: Test the 12 required behaviors**

Add model-controlled integration tests for outcome assertion, nonexistent gun, tutorial-and-lock constraint, stealth assertion, NPC resistance, NPC putative intent, belief isolation, private perception, evidence conservation, autonomous departure, non-preset observation, and branch divergence.

**Step 3: Run complete verification**

Run: `yarn lint:story && yarn typecheck:story && yarn test:story && yarn test:web && yarn build:story && yarn build:web`

### Task 5: Manual Model-Backed Playtest

**Files:**
- Test artifact: durable project data in a temporary directory only

**Step 1: Configure an actual loopback OpenAI-compatible model bridge**

Use a real configured model profile, never a mocked transport, for a 30+ turn interactive session.

**Step 2: Verify restart and branches**

Restart the sidecar, reopen the branch head, and compare character state, resources, memories, world time, and checkpoint head. Create two branches from one checkpoint and demonstrate different outcomes.

**Step 3: Record evidence**

Retain only test output and documented commands; remove temporary runtime data after verification.
