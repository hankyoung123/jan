# Player Actor Identity Optimization Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make Player and NPC identities use the same stable `actor_id` / `display_name` semantics throughout simulation, durable history, recall, and prompts, while leaving natural second-person language only in dialogue or the existing player-facing projection.

**Architecture:** The `Character` data remains the identity owner and `display_name` is the only narrative identity. `ProjectRuntimeFactory` injects that name into each Character recipe, while `StorySimulationRuntime` frames every Actor's raw expression with the Actor display name before resolution or persistence. Existing `PerceptionBuilder` remains the presentation owner; no identity manager, pronoun resolver, context rewriter, memory type, or Agent is introduced.

**Tech Stack:** Python 3.12, Pydantic, Concordia, FastAPI simulation runtime, pytest, Ruff, mypy.

---

## End-state design

- **Owner:** `Character.display_name` and existing Actor/Context rendering.
- **Change:** The fixed Player seed uses `actor_id="player"` and `display_name="陈默"`; Character authority is rendered with the current Actor's name; Actor intents are stored as named quoted expressions; GM character context requires display names in narration while allowing pronouns inside dialogue.
- **Delete:** Player narrative aliases such as `你`, `你（来访者）`, `User`, and `human` from simulation data and context. Remove the unnamed Character authority text.
- **New:** No semantic subsystem. A small renderer on `StorySimulationRuntime` frames all Actor intents uniformly because both human and model-controlled Actors require the same durable identity boundary.
- **Presentation boundary:** Player-visible responses may still contain UI-authored second-person copy, and dialogue may naturally contain `你`; neither is converted back into Memory, Wiki, Event, or Actor State.

### Task 1: Establish the three identity failures

**Files:**
- Modify: `apps/story-engine/tests/concordia_runtime/test_factory.py`
- Modify: `apps/story-engine/tests/simulation/test_interactive_npc_handoff.py`
- Modify: `apps/story-engine/tests/concordia_runtime/test_long_term_memory.py`

1. Add one NPC context test proving `Role: 林澈` and stable `陈默` references without a narrative `你` alias.
2. Add one durable history test proving a Player event and routed Memory use `陈默` rather than `你`.
3. Add one cross-Actor recall test proving 林澈 and 张野 recall the same named Player event.
4. Run only those tests and confirm that the current implementation fails for the intended reasons.

### Task 2: Bind Character authority to display_name

**Files:**
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/factory.py`
- Modify: `apps/story-engine/src/story_engine/simulation/factory.py`
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/replay_scenario.py`
- Update existing call sites under `apps/story-engine/tests/`

1. Require `display_name` when constructing the default Character recipe.
2. Render the exact role authority using that display name in every sentence.
3. Preserve the current-authority-on-restore behavior so old checkpoints receive the current named prompt.
4. Run the focused factory tests.

### Task 3: Remove Player aliases from canonical seed data

**Files:**
- Modify: `apps/story-engine/src/story_engine/submission/service.py`
- Modify: `apps/story-engine/src/story_engine/submission/discussion.py`
- Modify: affected submission and perception tests

1. Change the fixed Player to `display_name="陈默"`.
2. Rewrite facts, relationships, identities, and scene narration to use stable character names.
3. Keep `“你怎么来了？”` unchanged because it is dialogue.
4. Tell the existing submission editor to author named narrative identities and never use a pronoun as a character display name.

### Task 4: Bind all Actor intents before durable history

**Files:**
- Modify: `apps/story-engine/src/story_engine/simulation/runtime.py`
- Modify: `apps/story-engine/src/story_engine/concordia_runtime/resolver.py`

1. Add one existing-owner rendering method that frames raw Actor expression with `actor.display_name` without rewriting its contents.
2. Use it for both human and model-controlled Actor putative actions, stage summaries, `StepResult.action_text`, and `ResolvedTurn.putative_event_text`.
3. Add the narrative display-name rule to the existing GM character registry context; allow natural pronouns only inside dialogue.
4. Keep structured `actor_id`, participant IDs, visibility, and persistence schemas unchanged.

### Task 5: Verify presentation and persistence boundaries

**Files:**
- Inspect: `apps/story-engine/src/story_engine/simulation/perception.py`
- Inspect: `apps/story-engine/src/story_engine/wiki/reader.py`
- Inspect: `apps/story-engine/src/story_engine/persistence/`

1. Confirm player-facing copy is the only non-dialogue owner allowed to emit `你`.
2. Confirm Wiki and Character recall consume stable event or observation text, not UI projections.
3. Run the three high-signal tests, relevant suites, Ruff, strict mypy, and the full pytest suite.
4. Rebuild and real-play the packaged desktop app with a fresh default project, then restore or safely preserve prior local data.
