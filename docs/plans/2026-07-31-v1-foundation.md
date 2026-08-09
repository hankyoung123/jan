# V1 Foundation Implementation Plan

> **Historical product plan.** Platform work may remain reusable, but the old
> navigation and AI novel-authoring scope are superseded by the Living Story
> World PRD and ADR-0009.

> **For Codex:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this
> plan task-by-task without subagent delegation.

**Goal:** Build a tested local vertical slice from desktop shell through an
authenticated FastAPI sidecar to Markdown-backed project creation and immutable
event commit.

**Architecture:** A pnpm monorepo hosts a React/Tauri desktop client and shared
contracts. A Python 3.12 FastAPI sidecar owns domain state and writes validated
Markdown through an atomic workspace layer. Candidate generation stays
replaceable and deterministic until model adapters are added.

**Tech Stack:** React 19, TypeScript, Vite, Tauri 2, FastAPI, Pydantic 2, uv,
pytest, Ruff, mypy, Vitest, Testing Library.

---

### Task 1: Repository baseline

**Files:**
- Create: `README.md`
- Create: `docs/architecture.md`
- Create: `docs/domain-model.md`
- Create: `docs/data-contracts.md`
- Create: `docs/adr/0001-platform-and-upstream-locks.md`
- Create: `THIRD_PARTY_NOTICES.md`
- Create: `licenses/README.md`

**Steps:**
1. Initialize `main` and register Jan as `upstream`.
2. Fetch the exact Jan `v0.8.4` tag.
3. Record Jan and Concordia commits and license status.
4. Verify `git remote -v`, `git rev-parse v0.8.4`, and all required docs.

### Task 2: Python service contract

**Files:**
- Create: `apps/story-engine/pyproject.toml`
- Create: `apps/story-engine/src/story_engine/config.py`
- Create: `apps/story-engine/src/story_engine/api/app.py`
- Test: `apps/story-engine/tests/api/test_health.py`
- Test: `apps/story-engine/tests/api/test_auth.py`

**Steps:**
1. Write a failing test asserting the exact `/health` payload.
2. Run `uv run pytest tests/api/test_health.py -v` and confirm failure.
3. Implement the app factory and health schema.
4. Write a failing test for missing, invalid, and valid bearer tokens.
5. Add constant-time token validation to protected API routes.
6. Run API tests and then `ruff check` and `mypy`.

### Task 3: Domain contracts

**Files:**
- Create: `apps/story-engine/src/story_engine/domain/models.py`
- Create: `apps/story-engine/src/story_engine/domain/errors.py`
- Test: `apps/story-engine/tests/domain/test_models.py`

**Steps:**
1. Write failing tests for active-character goals, intent outcome wording
   boundaries, candidate status transitions, and immutable events.
2. Implement `Character`, `WorldState`, `StoryEvent`, `TurnCandidate`,
   `CharacterIntent`, `WorldOutcome`, `ReviewResult`, and `StateChange`.
3. Run domain tests and inspect serialized JSON for contract stability.

### Task 4: Markdown workspace

**Files:**
- Create: `apps/story-engine/src/story_engine/workspace/atomic.py`
- Create: `apps/story-engine/src/story_engine/workspace/markdown.py`
- Create: `apps/story-engine/src/story_engine/workspace/project_store.py`
- Create: `apps/story-engine/src/story_engine/workspace/event_store.py`
- Test: `apps/story-engine/tests/workspace/test_atomic.py`
- Test: `apps/story-engine/tests/workspace/test_stores.py`

**Steps:**
1. Write failing tests proving atomic replacement and front-matter validation.
2. Implement sibling temporary writes, flush, `fsync`, and `os.replace`.
3. Write failing tests proving unconfirmed turns do not touch formal paths and
   existing event files cannot be overwritten.
4. Implement project creation and append-only event persistence.
5. Delete derived cache in a fixture and prove state reloads from Markdown.

### Task 5: Commit service and API

**Files:**
- Create: `apps/story-engine/src/story_engine/events/commit.py`
- Create: `apps/story-engine/src/story_engine/api/routes/projects.py`
- Create: `apps/story-engine/src/story_engine/api/routes/turns.py`
- Test: `apps/story-engine/tests/events/test_commit.py`
- Test: `apps/story-engine/tests/api/test_projects.py`

**Steps:**
1. Write failing tests for missing approval and stale world/character versions.
2. Implement `EventCommitService` as the only formal mutation entry point.
3. Add project create/read and turn create/review/approve/discard endpoints.
4. Verify rejected commits leave byte-identical formal Markdown.
5. Verify successful commits update event, character, and world consistently.

### Task 6: Contracts workspace

**Files:**
- Create: `packages/contracts/openapi.json`
- Create: `packages/contracts/src/generated.ts`
- Create: `scripts/generate-contracts.mjs`

**Steps:**
1. Export sorted OpenAPI JSON from the app factory.
2. Generate TypeScript declarations from committed OpenAPI.
3. Add a check command that fails on generated differences.
4. Run generation twice and verify a clean Git diff.

### Task 7: Desktop product shell

**Files:**
- Create: `apps/desktop/package.json`
- Create: `apps/desktop/src/app/App.tsx`
- Create: `apps/desktop/src/app/routes.tsx`
- Create: `apps/desktop/src/styles.css`
- Create: `apps/desktop/src-tauri/tauri.conf.json`
- Test: `apps/desktop/src/app/App.test.tsx`

**Steps:**
1. Write failing navigation and engine-status UI tests.
2. Implement the eight product destinations with a responsive Jan-derived
   operational layout and no Jan trademarks.
3. Add typed health polling and explicit disconnected/restart states.
4. Implement light/dark themes and a collapsible inspector.
5. Run `pnpm lint`, `pnpm typecheck`, `pnpm test`, and `pnpm build`.
6. Start the app, capture desktop and mobile-width screenshots, and fix every
   overflow, overlap, contrast, or blank-state defect before delivery.

### Task 8: First vertical flow

**Files:**
- Create: `apps/desktop/src/features/submission/*`
- Create: `apps/desktop/src/features/evolution/*`
- Create: `apps/story-engine/src/story_engine/submission/service.py`
- Create: `apps/story-engine/src/story_engine/evolution/service.py`
- Test: matching frontend and backend feature tests

**Steps:**
1. Add deterministic submission fixtures for a two-character fog-harbor story.
2. Create a valid initial Markdown project without an outline.
3. Generate isolated intents, one resolver outcome, and one editor review.
4. Expose confirm, request revision, and discard only.
5. Confirm that only approval produces a formal event.
6. Run ten deterministic rounds and verify traceable versions.

### Task 9: Quality gate

**Steps:**
1. Run all frontend and Python checks from a clean dependency install.
2. Run contract drift verification.
3. Run Cargo formatting, Clippy, and a Tauri build smoke test.
4. Verify no secret-like values appear below a generated project directory.
5. Record incomplete V1 phases and their acceptance gaps without claiming them
   complete.
