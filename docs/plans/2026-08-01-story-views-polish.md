# Story Views Polish Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Consolidate the duplicated Story view toggle into one reusable component and add an explicit empty-edge state to the character relationship graph.

**Architecture:** Both `EventsView` and `CharactersView` currently render the same segmented control with different labels and icons. Extract a `StoryViewToggle` component that accepts options and keeps the existing Jan/shadcn visual language. The relationship graph then gets a visible empty-edge hint while still showing character nodes.

**Tech Stack:** React, TypeScript, Tailwind CSS, lucide-react, Vitest.

---

### Task 1: Extract `StoryViewToggle`

**Files:**
- Modify: `web-app/src/features/story/StoryViews.tsx`
- Test: `web-app/src/features/story/StoryViews.test.tsx`

**Step 1: Write the failing test**

Add a test that renders `EventsView`, switches to the Story Map, and verifies the toggle still reports the correct pressed state. The existing Story Map test already covers this; add an assertion that the `aria-pressed` state flips.

**Step 2: Run test to verify it fails**

Run: `corepack yarn vitest run --project @janhq/web-app web-app/src/features/story/StoryViews.test.tsx`
Expected: PASS for the existing behavior, so this task is a refactor with unchanged behavior.

**Step 3: Implement `StoryViewToggle`**

Add a generic segmented control in `StoryViews.tsx`:

```tsx
function StoryViewToggle<T extends string>({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: T
  onChange: (value: T) => void
  options: Array<{ value: T; label: string; icon: LucideIcon }>
}) {
  return (
    <div
      aria-label={label}
      className="flex rounded-md border bg-background p-0.5"
      role="group"
    >
      {options.map((option) => {
        const Icon = option.icon
        return (
          <button
            aria-pressed={value === option.value}
            className={`inline-flex h-8 items-center gap-2 rounded px-3 text-sm ${
              value === option.value
                ? 'bg-accent font-medium'
                : 'text-muted-foreground hover:bg-accent/60'
            }`}
            key={option.value}
            onClick={() => onChange(option.value)}
            type="button"
          >
            <Icon size={15} />
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
```

Replace the duplicated markup in `EventsView` and `CharactersView`.

**Step 4: Run tests to verify**

Run: `corepack yarn vitest run --project @janhq/web-app web-app/src/features/story/StoryViews.test.tsx`
Expected: 28 tests pass.

### Task 2: Add empty-edge state to the relationship graph

**Files:**
- Modify: `web-app/src/features/story/StoryViews.tsx`
- Test: `web-app/src/features/story/StoryViews.test.tsx`

**Step 1: Write the failing test**

Add a test that renders the graph for `projectSnapshot` (no relationships) and expects `暂无关系连线`.

**Step 2: Run test to verify it fails**

Expected: FAIL because the text does not exist yet.

**Step 3: Implement the empty-edge hint**

In `CharacterRelationshipGraph`, render a hint when `edges.length === 0`:

```tsx
{edges.length === 0 && (
  <p className="mb-4 text-sm text-muted-foreground">暂无关系连线</p>
)}
```

**Step 4: Run tests to verify**

Run: `corepack yarn vitest run --project @janhq/web-app web-app/src/features/story/StoryViews.test.tsx`
Expected: PASS.

### Task 3: Full verification

**Step 1:** Run `corepack yarn workspace @janhq/web-app lint`; expected PASS.
**Step 2:** Run `corepack yarn workspace @janhq/web-app build`; expected PASS.
**Step 3:** Run `git diff --check`; expected PASS.

Commit steps are deferred until the user approves committing the dirty worktree.
