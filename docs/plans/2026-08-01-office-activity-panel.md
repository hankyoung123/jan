# Office Activity Panel Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a lightweight office activity panel to the evolution page so active characters, locations, and current actions are visible with a subtle animated state.

**Architecture:** The panel lives inside `EvolutionView` and reads data already loaded there (`project.characters`, `candidate.intents`, `workingAction`). It uses the existing Jan/shadcn layout, lucide icons, and CSS animation classes; no new dependencies or backend changes.

**Tech Stack:** React, TypeScript, Tailwind CSS, lucide-react, Vitest.

---

### Task 1: Build `OfficeActivityPanel`

**Files:**
- Modify: `web-app/src/features/story/StoryViews.tsx`
- Test: `web-app/src/features/story/StoryViews.test.tsx`

Add a panel component that renders one card per active character. Each card shows the character name, location, and either the current intent action or a waiting/generating placeholder. The status dot pulses for characters with an action in the current candidate.

Insert the panel after the progress steps in `EvolutionView`.

### Task 2: Update evolution tests

Update existing name assertions to allow duplicates, then add a test that covers:
- panel header `角色行动中`;
- character locations `天线塔` and `生命支持舱`;
- `等待行动` before generation;
- intent actions after clicking `生成角色行动`.

### Task 3: Verify

Run `StoryViews.test.tsx`, Web lint, Web build, and `git diff --check`; all must pass.

Commit steps are deferred until the user approves committing the dirty worktree.
