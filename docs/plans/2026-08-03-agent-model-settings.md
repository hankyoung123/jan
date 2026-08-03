# Agent Model Settings Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Provide a working model settings surface for Story Engine task agents and per-character agents, including safe migration of existing model configuration and deterministic runtime resolution.

**Architecture:** `ProfileRegistry` remains the application-level owner of reusable, secret-free model profiles. A project-local `ProjectModelPolicyStore` owns task-profile defaults and per-agent profile assignments without adding infrastructure fields to canonical `Character` documents. Runtime construction resolves that policy once per session and records the selected profile IDs in durable session state. The Tauri model bridge receives explicit `provider/model` references only; there is no implicit model state shared with Jan chat.

**Tech Stack:** Python 3.12, Pydantic, FastAPI, React 19, TypeScript, TanStack Router, Tauri 2, Vitest, pytest.

---

### Task 1: Repair the global profile registry contract

**Files:**
- Modify: `apps/story-engine/src/story_engine/models/registry.py`
- Modify: `apps/story-engine/src/story_engine/models/contracts.py`
- Test: `apps/story-engine/tests/models/test_registry.py`
- Test: `apps/story-engine/tests/models/test_gateway.py`

1. Add failing tests for schema 3 migration, explicit `provider/model` references, backup creation, and rejection of model-less requests.
2. Run the focused registry and gateway tests and confirm the new assertions fail.
3. Implement one-time schema 3 migration to schema 4, retaining supported task settings, mapping the old projection task to wiki maintenance, and writing a recoverable backup before replacement.
4. Require a non-empty explicit model reference for executable model requests; remove the false default-inheritance behavior.
5. Run the focused tests.

### Task 2: Add project-owned agent model policy

**Files:**
- Create: `apps/story-engine/src/story_engine/domain/model_policy.py`
- Create: `apps/story-engine/src/story_engine/models/policy.py`
- Modify: `apps/story-engine/src/story_engine/workspace/project_store.py`
- Test: `apps/story-engine/tests/models/test_policy.py`

1. Add failing tests for default task bindings, per-character overrides, unknown characters, task/profile mismatches, atomic persistence, and branch-independent project ownership.
2. Implement immutable policy contracts and an atomic project-local store under `.story-engine/config/model-policy.json`.
3. Validate assignments against both the project character roster and `ProfileRegistry`.
4. Run the policy tests.

### Task 3: Expose policy APIs and runtime resolution

**Files:**
- Modify: `apps/story-engine/src/story_engine/api/routes/models.py`
- Modify: `apps/story-engine/src/story_engine/api/app.py`
- Modify: `apps/story-engine/src/story_engine/simulation/factory.py`
- Modify: `apps/story-engine/src/story_engine/domain/session_manifest.py`
- Test: `apps/story-engine/tests/api/test_model_routes.py`
- Test: `apps/story-engine/tests/simulation/test_factory.py`
- Test: `apps/story-engine/tests/persistence/test_session_store.py`

1. Add authenticated GET/PUT project model-policy API tests.
2. Add runtime tests proving different characters can resolve different actor profiles while GM uses the project task binding.
3. Implement API validation and runtime resolution.
4. Persist the resolved profile assignment snapshot in `SessionManifest` so restarts and diagnostics retain provenance.
5. Run API, simulation, and persistence tests.

### Task 4: Make model settings accessible and unambiguous

**Files:**
- Modify: `web-app/src/features/story/ModelProfiles.tsx`
- Create: `web-app/src/routes/settings/story-models.tsx`
- Modify: `web-app/src/constants/routes.ts`
- Modify: `web-app/src/components/left-sidebar/navigation.ts`
- Test: `web-app/src/features/story/ModelProfiles.test.tsx`
- Test: `web-app/src/routes/__tests__/story-models.test.tsx`

1. Add failing UI tests for a reachable settings route, provider-qualified option values, required task assignments, and per-character overrides.
2. Refactor the existing orphaned component into a settings page surface that loads global profiles, the active project, and its policy.
3. Store `provider/model` values, retain provider identity for duplicate model IDs, and show actionable empty/error states.
4. Save global task profiles and project agent assignments through their authoritative APIs.
5. Run focused Vitest and TypeScript checks.

### Task 5: Verify the complete path and rebuild distribution artifacts

**Files:**
- Modify as required by failing integration coverage only.

1. Add a bridge contract test proving every Story Engine completion has an explicit model and duplicate model IDs route by provider.
2. Run Story Engine tests, web tests, Rust tests, lint, mypy, TypeScript, and `git diff --check`.
3. Build the Sidecar, web application, `.app`, and `.dmg` with `yarn build`.
4. Verify the bundled Sidecar, licenses, application signature structure, and read-only mounted DMG contents.
