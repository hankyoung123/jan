# Wiki Lifecycle and CI Recovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the working Wiki the latest reachable projection, keep historical Wiki versions immutable, make automatic manuscript persistence atomic with lineage validation, restrict rebuild to the current branch head, and prove every CI gate green.

**Architecture:** `WikiStore` owns the single reachable-projection validity rule and immutable version writes. `WikiBoundaryProcessor` rebuilds only from immutable reachable baselines and checkpoint-authoritative logs, while `ProjectionTaskService` always repairs the current branch head instead of replaying an old task against live pages. `ManuscriptService` keeps generation and review in memory and delegates one guarded final write to `SceneDraftStore`.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, pytest, TypeScript/OpenAPI contracts, React/Vitest, Rust/Tauri.

---

### Task 1: Centralize reachable Wiki validity

**Files:**
- Modify: `apps/story-engine/src/story_engine/wiki/store.py`
- Modify: `apps/story-engine/src/story_engine/wiki/context.py`
- Modify: `apps/story-engine/src/story_engine/wiki/lint.py`
- Test: `apps/story-engine/tests/concordia_runtime/test_long_term_memory.py`
- Test: `apps/story-engine/tests/wiki/test_lint.py`

**Step 1: Write the failing ancestor-availability and rollback-exclusion tests**

Create a real checkpoint lineage where the working Wiki points to an ancestor. Assert Actor and GM contexts include it while the checkpoint remains in the head lineage, then roll back before that checkpoint and assert the same Wiki is unavailable.

**Step 2: Run the focused tests and verify exact-head behavior fails**

Run:

```bash
apps/story-engine/.venv/bin/pytest -q \
  apps/story-engine/tests/concordia_runtime/test_long_term_memory.py \
  apps/story-engine/tests/wiki/test_lint.py
```

Expected: ancestor Wiki is rejected and linter reports it as behind.

**Step 3: Implement one Store-owned validity rule**

Add a `WikiStore.is_reachable_projection()` method with this logic:

```python
if view.stale or view.degraded:
    return False
if view.checkpoint_id is None:
    return True
return BranchStore(root).checkpoint_is_reachable(branch_id, view.checkpoint_id)
```

Make `WikiContextBuilder` and `WikiLinter._check_head()` use it. Delete exact-head and step-equality validity checks.

**Step 4: Run focused tests**

Expected: reachable ancestor and seed pass; abandoned, stale, and degraded projections fail.

### Task 2: Make historical Wiki versions immutable

**Files:**
- Modify: `apps/story-engine/src/story_engine/wiki/store.py`
- Modify: `apps/story-engine/src/story_engine/wiki/boundary.py`
- Modify: `apps/story-engine/src/story_engine/simulation/projections.py`
- Test: `apps/story-engine/tests/simulation/test_projection_recovery.py`

**Step 1: Write the historical immutability test**

Build Wiki versions at checkpoints 40 and 80, retry the old task at 40, and compare the bytes below both version directories before and after retry.

**Step 2: Verify the current implementation overwrites an existing version**

Run the new test and expect a byte/content mismatch for Wiki@40.

**Step 3: Delete overwrite behavior**

When a version index already exists, omit every write below that version directory. New versions use `overwrite=False` for all pages and the version index.

**Step 4: Route task retry/recovery through current-head rebuild**

For a reachable Wiki task, load the branch's current head snapshot and invoke chronological rebuild. Never call `process(old_task_snapshot)` against live pages.

**Step 5: Run Wiki boundary, store, projection recovery, and integration tests**

Expected: old versions are byte-identical and current working Wiki resolves to the latest reachable version.

### Task 3: Persist automatic manuscript output once

**Files:**
- Modify: `apps/story-engine/src/story_engine/manuscript/service.py`
- Modify: `apps/story-engine/src/story_engine/workspace/scene_store.py`
- Modify: `apps/story-engine/tests/api/test_simulation_integration.py`
- Test: `apps/story-engine/tests/manuscript/test_service.py`

**Step 1: Write the rollback-during-review test**

Use an agent whose review hook rolls the branch back. Assert the reachability precondition fails and the draft directory remains empty.

**Step 2: Verify the current early draft write fails the test**

Expected: one orphan `drafts/scene-*.md` exists.

**Step 3: Keep intermediate state in memory**

Build the draft, run review, construct the reviewed draft, then call one save. Remove the save before review.

**Step 4: Put validation inside the atomic write lock**

Extend `SceneDraftStore.save()` with an optional precondition and commit its one file through `AtomicBatch.commit(precondition=...)`.

**Step 5: Update the obsolete editor-failure assumption**

An editor failure must now leave zero drafts, not one unreviewed draft.

### Task 4: Restrict rebuild API to current head

**Files:**
- Modify: `apps/story-engine/src/story_engine/api/routes/wiki.py`
- Modify: `web-app/src/features/story/world/useWiki.ts`
- Modify: `apps/story-engine/tests/contracts/test_openapi.py`
- Modify: relevant API tests under `apps/story-engine/tests/api/`
- Regenerate: `packages/contracts/openapi.json`
- Regenerate: `packages/contracts/src/generated.ts`

**Step 1: Remove `WikiRebuildRequest` and its checkpoint field**

The endpoint loads `branch.head_checkpoint_id` only and accepts no request body.

**Step 2: Remove pre-validation mutation**

Delete `store.mark_stale(...)` before rebuild. Rebuild's atomic preconditions remain responsible for rejecting a moved head.

**Step 3: Update the Web caller and contracts**

POST without a JSON body, then run `yarn contracts:generate`.

**Step 4: Verify OpenAPI exposes no rebuild request body or historical checkpoint input**

### Task 5: Targeted verification

**Files:**
- Verify only; no new files.

Run:

```bash
apps/story-engine/.venv/bin/ruff check apps/story-engine/src apps/story-engine/tests
apps/story-engine/.venv/bin/mypy --config-file apps/story-engine/pyproject.toml apps/story-engine/src/story_engine
apps/story-engine/.venv/bin/pytest -q apps/story-engine/tests
yarn contracts:check
yarn workspace @story-engine/contracts typecheck
```

Expected: all pass.

### Task 6: Full CI gates

**Files:**
- Modify only the authoritative owner of a reproduced failure.

Run the workflow-equivalent gates:

```bash
yarn lint:jan
yarn test:jan
yarn test:visual
yarn build:web
rustfmt --check --edition 2021 src-tauri/src/core/story_engine_runtime.rs src-tauri/src/core/story_model_bridge.rs
cargo test --manifest-path src-tauri/Cargo.toml --no-default-features --features test-tauri story_engine_runtime::tests --lib
cargo clippy --manifest-path src-tauri/Cargo.toml --all-targets --no-default-features --features test-tauri -- -D warnings
yarn tauri build --no-bundle -- --no-default-features --features test-tauri
```

Expected: Generated Contracts, Python Story Engine, Web, and Desktop Rust/Tauri all pass without compatibility adapters.

### Task 7: Completion audit

**Files:**
- Inspect the final diff, version directories produced by tests, OpenAPI, and CI outputs.

Confirm requirement-by-requirement:

```text
owner: Wiki lineage validity / projection persistence
change: reachable ancestor + immutable versions + atomic manuscript + head-only rebuild
delete: exact-head + version overwrite + early draft write + rebuild checkpoint body
new: no subsystem, state owner, cache, database, manager, or compatibility layer
```

Only then mark the goal complete.
