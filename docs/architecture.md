# Living Story World Architecture

This document translates the
[Living Story World PRD v1.0](product-plan.md) into current engineering
boundaries. The PRD is authoritative when this document, an older ADR, or
retained implementation disagrees with it.

## Product protocol

The product exposes one event protocol with two Game Master entry modes:

    World -> Perception -> Intent -> Resolution -> ResolvedEvent
       ^                                                |
       |------------ Actor state + Memory --------------|

- **World** is durable objective reality: time, locations, rules, environment,
  hidden facts, important objects, pressure, and committed history.
- **Actor** is the shared player/NPC abstraction. Both submit putative Intent
  and obey the same Resolution boundary.
- **Perception** is a restricted projection for one Actor, never a World dump.
- **Intent** is free natural language and always means an attempt.
- **Resolution** is the sole semantic adjudication boundary.
- **Memory** records an Actor's past experience; it is not current Actor state.

Only a validated **ResolvedEvent** may commit a world consequence. Input,
actor output, intent, narrative text, belief, perception, and model reasoning
cannot directly mutate World Truth.

World-owned Clock/Pressure triggers may enter the same Game Master through
Initiative Mode and also produce a ResolvedEvent. This is not a second Agent,
engine, history, or workflow.

## Runtime ownership

Jan remains the desktop and model-infrastructure foundation. React renders the
World Session and derived views. Tauri owns desktop lifecycle and the private
model bridge. The Python sidecar owns story-domain validation, Concordia
adaptation, Resolution, durable state, branches, and restricted projections.

Unmodified gdm-concordia 2.4.0 is the only Entity/Component/Engine
implementation. Product code adds prefabs, recipes, context components,
memory codecs, persistence, API application services, and validation. Do not
create a PlayerEngine, WorldEngine, ResolutionEngine, CombatEngine,
InventoryEngine, or parallel simulation loop.

    React World Session -> FastAPI -> StorySimulationRuntime
                                  -> Concordia Sequential + Game Master
                                  -> ModelGateway -> Jan provider/runtime

ModelGateway is the only story-domain model boundary. Provider credentials and
protocols remain outside project files and domain objects.

## Resolution and effects

Human and NPC actions enter the same putative-action path. The Game Master
receives one bounded `ResolverContext` containing Current Intent, affected
Actor State, relevant World facts, and a small recent causal window. It excludes
pressures, clocks, full world-variable dumps, and plot-pacing instructions.
Resolution keeps three distinct views: what the World
knows, what the acting Actor knows, and what that Actor merely believes.

Relevant Canonical Truth is selected deterministically from the immutable Fact
seed using the acting Actor, current location, intent, known facts, participants,
and recent events. The full world truth is never dumped into a prompt. Game
Master-only facts may constrain Resolution without entering Actor Knowledge or
player-visible output.

`Character` is the sole current-state owner for every Actor. The lightweight
`ActorStateContext` deterministically projects that record into Concordia
immediately before action and Resolution, and is rebuilt from the checkpoint
after restore. It is context, not a second mutable state store. The Game Master
receives this projection directly; Wiki and retrieval are not on the
authoritative Resolution path and are not fields of `ResolverContext`.
Resolution remains valid when Wiki is empty, missing, stale, or malformed.

Structured effects are deliberately narrow. They may update only store-owned
World or Actor state after validation. A resource effect must transfer or
consume an established resource; it cannot materialize a weapon, key,
capability, or decisive clue from actor text. A belief update never changes the
corresponding World fact.
GM-authored state updates cannot target `beliefs` or `current_goal`, including
for the currently acting Actor. Those fields, Intent, voluntary dialogue, and
voluntary action remain Actor-owned. An active Character may temporarily have
no `current_goal`; its required `core_desire` remains persistent.
When the player explicitly states “我认为……” (or an equivalent belief), the
runtime records the belief as a deterministic, source-linked Actor state effect
in the same committed turn. It does not alter the corresponding World Truth.

Evidence Conservation is a Resolution invariant. Runtime generation may add
ordinary environmental texture, but it cannot invent a decisive clue,
witness, secret route, alibi, or causal fact because an Actor inspected
something. The default world starts from explicit facts and knowledge
boundaries without a preset culprit or fixed ending.

## Perception and privacy

Perception is built from the selected checkpoint, the requesting Actor's state
and memory boundary, and visible committed events. It excludes:

- another Actor's private memory or internal state;
- Game Master hidden facts and reasoning;
- undiscovered evidence;
- events outside the Actor's visibility;
- raw prompts, traces, provider details, and complete runtime snapshots.

Glance, Observe, and Inspect are natural-language depths of Intent, not
separate engines or object-click modes.

## Durable authority

The authoritative runtime data is:

- validated ResolvedEvents;
- immutable checkpoints and branch lineage;
- current World and Actor state captured by the checkpoint;
- shared and private Memory required to restore the simulation.

The branch manifest identifies the current head. A commit writes and verifies
the checkpoint and event/trace record before atomically advancing that head.
Failed or cancelled model calls cannot advance it. One live state-mutating
session is allowed per project branch.

An interactive UI turn uses independently idempotent commands: the player step
commits first, then an eligible NPC step commits if one is scheduled, then a
World Initiative step may commit if deterministically triggered. Each
successful child command has its own receipt and checkpoint. The API may merge
their visible events for display, but never persists that merged response as a
third history record.

## Game Master initiative mode

One Game Master has two modes. Resolution Mode adjudicates an Actor Intent and
does not receive plot-pacing state. Initiative Mode receives pressures, clocks,
relevant World state, and recent causal events, and asks only what external
change happens now. It cannot decide Actor cognition or voluntary behavior.

The deterministic trigger priority is due Clock, due Pressure, then stagnation
after roughly three committed Actor turns without material World change. A
pending NPC response blocks Initiative. A two-turn cooldown prevents immediate
repetition. Code decides whether to call the GM; it does not score excitement,
tension, or boredom. Successful Initiative output follows the normal candidate
validation and atomic ResolvedEvent commit path.

Wiki, Narrative, UI Scene, Summary, and Manuscript are projections. They may
be rebuilt, edited under their own workflow, or fail independently without
becoming world truth. Project seed material can initialize a world, but only
Resolution can commit runtime consequences. Provider cache is never authority.

The physical serialization format may evolve behind validated stores. Product
code must not expose file format as a second semantic authority or require a
projection to restore a branch.

## Time, persistence, and branching

World time is committed state. Resolution advances it by a plausible amount;
other Actors may act, leave, refuse, miss opportunities, or change plans while
the player investigates. MVP does not run a real-time 24-hour background
simulation and does not add a separate Time Engine.

A checkpoint is an immutable history node. Restoring resumes that world state.
Continuing from an older checkpoint creates a new Branch with independent
future events; it never overwrites the original history. Reopening a project
must derive the current Perception from its saved branch head, the most recent
reachable Scene Boundary, subsequent visible committed events, and current
location/time. The initial scene prose is only an initialization fallback.

## NPC participation

Important NPCs are Actors with private knowledge, goals, conditions,
relationships, resources, memory, and location. They submit Intent and cannot
commit outcomes directly. The runtime may select only the Actors who need to
decide during a turn; the product does not promise that every NPC receives a
model call every cycle.

The player is always present in an interactive scene roster. Roster planning
selects zero to three NPCs; autonomous sessions retain the general one-to-four
Actor rule.

Lightweight background people may remain environment records until they need
persistent agency. That optimization must not create a different Resolution
rule, require an Editor Agent, or impose the old npc-to-active authoring
lifecycle on the World Session.

Ordinary dynamically mentioned NPCs remain NPCs across scene boundaries. The
World Session does not run `AutomaticPromotionReviewer`, create promoted Actor
memory, or edit Wiki pages as a side effect of ordinary play.

## MVP surface

The required UI is a world-like World Session with:

- current time and location;
- scene/perception prose without chat bubbles;
- one free-form “说出你想做的事……” input;
- Self Lens;
- Timeline and Branch controls.

Writer, Editor, submission chat, chapter generation, manuscript editing,
story-map views, RAG workspaces, action menus, quest systems, inventories,
skill engines, combat engines, and 24-hour simulation are outside the MVP core.
Retained implementations may continue to exist, but the World Session must not
depend on them.

## Observability

Traces may record model profile, provider/model reference, prompt version and
hash, authorized context sources, token use, duration, retry, and structured
errors. Traces are audit data, not world history, and must not expose secrets
or private reasoning through player-facing APIs.
