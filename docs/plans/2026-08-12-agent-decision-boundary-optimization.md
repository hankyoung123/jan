# Agent Decision Boundary Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use executing-plans to implement this plan task-by-task.

**Goal:** Make each simulation model call receive only the facts and authority needed for its single decision, then verify the six fixed experience scenarios.

**Architecture:** Keep the existing Character, Game Master, resolver, and runtime owners. Replace mixed prompt context with one actor-specific Perception context, compress Character and GM authority text, and use the current scene's last four committed events instead of global memory/Wiki duplication. Structured schemas and deterministic runtime validation remain in code.

**Tech Stack:** Python 3.12, Pydantic, Concordia components, pytest, Ruff, mypy.

---

### Task 1: Lock the decision boundaries with focused tests

**Files:**
- Modify: `apps/story-engine/tests/concordia_runtime/test_factory.py`
- Modify: `apps/story-engine/tests/concordia_runtime/test_resolver.py`
- Modify: `apps/story-engine/tests/simulation/test_runtime_lifecycle.py`

1. Assert the exact Character authority prompt.
2. Assert Perception receives only current world, actor state, actor-known facts, and at most four directly visible current-scene events.
3. Assert GM semantic rules are present once and prompt-level schema/examples are absent.
4. Add the six fixed short experience cases and assert only committed GM output becomes fact.

### Task 2: Narrow Perception to actor-specific evidence

**Files:**
- Modify: `apps/story-engine/src/story_engine/domain/simulation.py`
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/factory.py`
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/prefabs/game_master.py`
- Modify: `apps/story-engine/src/story_engine/simulation/runtime.py`

1. Add one existing-component-style constant for the current actor's Perception context.
2. Build that text from current World State, Actor state/viewpoint, Actor-known facts, and up to four directly visible current-scene events.
3. Remove Pacing, global story instruction, Wiki, roster, and broad event history from MakeObservation.
4. Keep Perception authority limited to reporting what the actor can currently perceive.

### Task 3: Compress Character and GM resolution authority

**Files:**
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/factory.py`
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/prefabs/game_master.py`
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/resolver.py`
- Modify: `apps/story-engine/src/story_engine/domain/simulation.py`
- Modify: `apps/story-engine/src/story_engine/simulation/runtime.py`

1. Replace Character instruction with the exact approved Chinese text.
2. Reduce GM semantic instruction to the six approved rules.
3. Remove JSON shapes, field examples, enum details, and deterministic rules already owned by Schema/Code.
4. Remove Wiki, actor observation memory, and duplicate recent-event sections from ResolverContext; retain authoritative state, relevant canonical truth, Actor knowledge, current intent, and four current-scene events.

### Task 4: Verify behavior before broader quality gates

**Files:**
- Test: focused files above
- Test: `apps/story-engine/tests/concordia_runtime/`
- Test: `apps/story-engine/tests/simulation/`

1. Run the six fixed experience cases first.
2. Run focused prompt/context tests.
3. Run the relevant Concordia and simulation suites.
4. Run Ruff, mypy, and the repository's Python quality gate command.
5. Inspect actual recorded prompts from a short real simulation path and confirm no authority violation, knowledge leak, invented fact/resource/evidence, or unnatural Character decision.
