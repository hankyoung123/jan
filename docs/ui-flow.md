# World Session UI Flow

The MVP must feel like entering a world, not operating an AI writing tool or a
chat client.

## Primary session

    Enter or resume world
          ↓
    Current Perception
          ↓
    Free-form Intent
          ↓
    Resolution + ResolvedEvent
          ↓
    World / Actor / Memory update
          ↓
    Time advances + relevant NPC response
          ↓
    New Perception

The main surface shows current world time and location, scene prose, visible
people and changes, and one input labelled “说出你想做的事……”. It does not use
traditional chat bubbles.

Users may naturally act, speak, think, observe, wait, deceive, test, or change
plans in the same input. Do not require commands, action types, target IDs,
skill names, JSON, recommended choices, or an action menu.

## Perception depth

Glance is the automatic scene read, Observe is active attention, and Inspect
requires actual manipulation. These are natural-language depths of Intent, not
tabs, modes, or pixel-hunting interactions.

The rendered scene is always a restricted Perception. It cannot reveal private
NPC memory, hidden GM truth, undiscovered evidence, internal reasoning, or
another Actor's state merely because the UI has access to a full API client.

## Self Lens

“我现在是谁？” displays only established facts:

- identity;
- capabilities and experience;
- conditions;
- important possessions;
- important relationships.

It does not display HP, generic skill levels, success probability, RPG stats,
or strategic recommendations.

## Timeline and Branch

Timeline lists committed checkpoints using world time and meaningful event
context. The current node is clear. Selecting an earlier node offers
“从这里继续”, which creates a new Branch. It must not silently roll back or
overwrite the original history.

Reopening a project loads the selected Branch head and derives a fresh current
Perception. It never returns to the opening scene unless that checkpoint is
actually selected.

## Default world

The bundled MVP world is 《雨夜公寓》. It starts with explicit facts and
per-character knowledge boundaries, but no preset culprit or fixed ending.
UI exploration may reveal established information and generate harmless
environmental texture, but inspecting a visible object cannot manufacture key
evidence.

## Navigation boundary

For MVP, World Session is the primary destination. Self Lens, Timeline, Branch,
and model/provider configuration are supporting surfaces.

Workbench, Evolve controls, Characters, World Wiki, Events, Manuscript,
Submission, Writer/Editor review, RAG evidence, relationship graphs, and story
maps may remain visible while migration is unfinished, but they are legacy or
future surfaces. They must not define the primary user loop or become required
for World Session Resolution, persistence, perception, restore, or branching.

Model/provider settings reuse Jan infrastructure. They configure execution and
must not expose credentials, prompts, provider SDK details, or full runtime
state in the session UI.

## MVP exclusions

Do not add chat bubbles, action menus, quest lists, skill buttons, success
odds, combat UI, inventory-engine UI, achievements, multiplayer, voice,
runtime image generation, marketplaces, or a 24-hour background-world control
surface to the MVP.
