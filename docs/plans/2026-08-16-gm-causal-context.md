# GM Causal Context Fix Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure GM Resolution and Initiative always receive the current branch's immediate and recent committed `ResolvedEvent` lineage, independent of the clearable scene buffer.

**Architecture:** Add one read-only projection on `SimulationLogStore` that flattens the last four `ResolvedEvent` objects reachable from a supplied branch history head. `StorySimulationRuntime` resolves the current branch head through the existing `BranchStore` and `CheckpointStore` on demand, then uses that projection for both GM contexts; `_pending_scene_events` remains exclusively responsible for scene finalization, roster planning, projection, and actor perception within the active scene.

**Tech Stack:** Python 3.12, Pydantic, pytest, existing markdown-backed `SimulationLogStore` / `CheckpointStore` / `BranchStore` persistence.

---

### Task 1: Add the committed-event lineage projection

**Files:**
- Modify: `apps/story-engine/src/story_engine/persistence/simulation_log.py`
- Test: `apps/story-engine/tests/simulation/test_gm_causal_context.py`

**Step 1: Write the failing branch-lineage test**

Create a main history `A -> B`, fork at `A`, commit `C` on the fork, then assert the fork's recent committed event view is exactly `A, C` and excludes `B`.

**Step 2: Run the test to verify it fails**

Run: `uv run --project apps/story-engine pytest apps/story-engine/tests/simulation/test_gm_causal_context.py::test_recent_committed_events_follow_only_current_branch_lineage -q`

Expected: FAIL because the recent committed event query does not exist.

**Step 3: Implement the minimal projection**

Add `SimulationLogStore.recent_committed_events(history_head_id, *, branch_id, limit=4)`. Validate a positive limit, reuse `chain(...)`, flatten only committed `ResolvedTurn.events`, and return the final `limit` events without storing or caching them.

**Step 4: Run the test to verify it passes**

Run the same targeted pytest command.

Expected: PASS.

### Task 2: Make Resolution use committed causal history

**Files:**
- Modify: `apps/story-engine/src/story_engine/domain/simulation.py`
- Modify: `apps/story-engine/src/story_engine/simulation/runtime.py`
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/resolver.py`
- Test: `apps/story-engine/tests/simulation/test_gm_causal_context.py`

**Step 1: Write the two failing Resolution tests**

Add one test proving a committed event remains in Resolution context after a scene boundary clears `_pending_scene_events`, and one test proving the immediate previous committed event is directly present for the next intent while an uncommitted scene-buffer decoy is absent.

**Step 2: Run the tests to verify they fail**

Run: `uv run --project apps/story-engine pytest apps/story-engine/tests/simulation/test_gm_causal_context.py -q`

Expected: the Resolution assertions fail because context still reads `_pending_scene_events`.

**Step 3: Implement the current-branch query and Resolution fields**

Add a thin `StorySimulationRuntime.recent_committed_events(limit=4)` query that resolves `branch.head_checkpoint_id -> checkpoint.history_head_id -> SimulationLogStore.recent_committed_events`. Populate `ResolverContext.immediate_previous_committed_event` from the last event and `ResolverContext.recent_committed_events` from all four. Update the resolution prompt to render the immediate previous event explicitly and the bounded committed sequence, while preserving actor state, canonical facts, and current world state.

**Step 4: Run the Resolution tests**

Run both new Resolution test node IDs.

Expected: PASS.

### Task 3: Make Initiative use the same committed source

**Files:**
- Modify: `apps/story-engine/src/story_engine/domain/simulation.py`
- Modify: `apps/story-engine/src/story_engine/simulation/runtime.py`
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/resolver.py`
- Test: `apps/story-engine/tests/simulation/test_gm_causal_context.py`

**Step 1: Write the failing Initiative boundary test**

Commit an event, clear the scene buffer through a boundary, create Initiative context, and assert the pre-boundary committed event is still present.

**Step 2: Run the test to verify it fails**

Run: `uv run --project apps/story-engine pytest apps/story-engine/tests/simulation/test_gm_causal_context.py::test_initiative_uses_committed_history_after_scene_boundary -q`

Expected: FAIL because Initiative still reads `_recent_scene_event_texts()`.

**Step 3: Switch Initiative to committed history**

Populate `InitiativeContext.recent_committed_events` from the same runtime query and update both the Initiative prompt and its Resolution surrogate. Remove `_recent_scene_event_texts()` once neither GM path uses it; leave `_pending_scene_events`, actor perception, boundary accumulation, roster planning, and projection behavior intact.

**Step 4: Run all four high-signal tests**

Run: `uv run --project apps/story-engine pytest apps/story-engine/tests/simulation/test_gm_causal_context.py -q`

Expected: 4 passed.

### Task 4: Verify regressions and architectural constraints

**Files:**
- Modify only existing tests if field names require mechanical updates.

**Step 1: Run focused subsystem tests**

Run: `uv run --project apps/story-engine pytest apps/story-engine/tests/simulation/test_runtime_lifecycle.py apps/story-engine/tests/concordia_runtime/test_resolver.py apps/story-engine/tests/persistence/test_commit.py -q`

Expected: PASS.

**Step 2: Run static checks**

Run the project-configured formatter/linter/type checker for the touched Python files.

Expected: PASS with no new warnings.

**Step 3: Audit the requirements directly**

Search for `_recent_scene_event_texts`, GM uses of `_pending_scene_events`, and any newly introduced cache/store/history owner. Confirm Resolution and Initiative both call the same committed view, limit remains fixed at four, branch lineage comes from the durable head, and scene-buffer behavior remains present only for scene/perception responsibilities.

**Step 4: Run the complete Story Engine test suite**

Run: `uv run --project apps/story-engine pytest apps/story-engine/tests -q`

Expected: PASS.
