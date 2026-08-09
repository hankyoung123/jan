# World Simulation Completion Implementation Plan

> **Current derived MVP plan.** Execute only within the Living Story World PRD
> and ADR-0009 boundaries. The PRD remains authoritative if this plan differs.

**Goal:** Complete the World Session MVP's shared actor turn loop, durable branch replay, and required acceptance coverage without adding a second simulation system.

**Architecture:** `StorySimulationRuntime` remains the sole Concordia execution kernel. A player action is committed through its existing human intent path. When the updated World state gives an NPC a real reason to decide or respond, a GM-selected non-player action uses the same existing automatic-step path; otherwise no artificial NPC action is added. The API response derives visible events from the durable results. Branch selection rehydrates a checkpoint into a branch-bound session and the session UI projects existing branch/checkpoint records.

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

### Task 2: Commit Relevant NPC Responses Through the Shared Resolution Path

**Files:**
- Modify: `apps/story-engine/src/story_engine/simulation/runtime.py`
- Modify: `apps/story-engine/src/story_engine/simulation/engine.py`
- Modify: `apps/story-engine/src/story_engine/simulation/command_service.py`
- Modify: `apps/story-engine/src/story_engine/simulation/perception.py`
- Test: `apps/story-engine/tests/api/test_interactive_session.py`
- Test: `apps/story-engine/tests/simulation/test_runtime_lifecycle.py`

**Step 1: Write failing coverage**

Use a controllable runtime to prove a player input is followed by an eligible
NPC action when the current World requires that Actor to decide, that both
results have separate durable commits, and that the player response includes
only visible events. Also prove that no NPC action is fabricated when no Actor
needs to respond.

**Step 2: Reuse the automatic-step execution path**

Add an internal eligible-actor filter to the existing `execute_step` path. The
command service executes one human step and may follow it with one NPC-only
automatic step when the GM/runtime scheduling decision identifies a relevant
Actor. Mere NPC presence is insufficient. This does not add a new engine or
direct world mutation API.

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

#### Acceptance evidence

The forced cases below use the real Concordia resolver with a replay-controlled
model where resolution semantics are being checked. Runtime, API, and
perception tests exercise the relevant durable boundary. Case 09 is an MVP GM
protocol rule rather than a separate evidence engine, so its automated check
asserts the GM instruction directly; the real-model playtest below remains the
required behavioral validation.

| Case | Evidence |
| --- | --- |
| 01 outcome assertion | `test_model_controlled_forced_intents_commit_only_the_gm_resolution[Case 01-*]`; `test_case_01_interactive_turn_treats_asserted_death_as_an_intent` |
| 02 nonexistent gun | `test_case_02_resource_updates_are_atomic_and_cannot_materialize_a_gun` |
| 03 tutorial and locked door | `test_model_controlled_forced_intents_commit_only_the_gm_resolution[Case 03-*]` |
| 04 stealth assertion | `test_model_controlled_forced_intents_commit_only_the_gm_resolution[Case 04-*]` |
| 05 NPC resistance | `test_model_controlled_forced_intents_commit_only_the_gm_resolution[Case 05-*]` |
| 06 NPC putative intent | `test_model_controlled_forced_intents_commit_only_the_gm_resolution[Case 06-*]`; `test_cases_06_and_10_npc_intent_is_resolved_and_can_act_autonomously` |
| 07 belief isolation | `test_case_07_player_belief_changes_do_not_rewrite_world_truth` |
| 08 private perception | `test_case_08_perception_excludes_private_events_and_internal_state` |
| 09 evidence conservation | `test_resolution_instruction_conserves_decisive_evidence` |
| 10 autonomous departure | `test_cases_06_and_10_npc_intent_is_resolved_and_can_act_autonomously` |
| 11 non-preset observation | `test_model_controlled_forced_intents_commit_only_the_gm_resolution[Case 11-*]` |
| 12 branch divergence | `test_case_12_interactive_branch_resumes_without_moving_main` |

### Task 5: Manual Model-Backed Playtest

**Files:**
- Test artifact: durable project data in a temporary directory only

**Step 1: Configure an actual loopback OpenAI-compatible model bridge**

Use a real configured model profile, never a mocked transport, for three
interactive UI turns. The deterministic API suite retains the 31-turn
restart/reopen regression without spending Provider time on a long manual run.

**Step 2: Verify restart and branches**

Restart the sidecar, reopen the branch head, and compare character state, resources, memories, world time, and checkpoint head. Create two branches from one checkpoint and demonstrate different outcomes.

**Step 3: Record evidence**

Retain only test output and documented commands; remove temporary runtime data after verification.
