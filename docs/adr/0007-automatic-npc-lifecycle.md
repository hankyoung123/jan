# ADR-0007: Automatic NPC Lifecycle

## Status

Superseded for the World Session by ADR-0009

This record describes the earlier authoring runtime's npc-to-active promotion
workflow. The current product treats the player and important NPCs as Actors
under one Intent/Resolution rule and does not require Editor promotion for MVP.
Its branch identity and model-call optimization ideas may be reused only when
they do not recreate that product-level lifecycle.

## Context

The runtime previously maintained project NPC files and separate dynamic entity
definitions. A dynamic entity immediately received an Actor and model, while
the product definition says ordinary people are not Agents. Promotion also
required a second user confirmation after the Editor had already reviewed it.
This produced two lifecycles, ambiguous archive semantics, branch leakage, and
unregistered observation owners reaching Character Wiki maintenance.

## Decision

Use one branch-local `Character` projection with the monotonic lifecycle
`npc -> active -> retired`. Scene participation is represented separately by
`roster_actor_ids`.

The Game Master can create only an ordinary `npc`. At a completed scene
boundary, the Editor automatically evaluates participating NPCs. Promotion
requires a concrete goal and event IDs from that scene. A successful decision
is recorded in the Step log, changes the Character to `active`, creates its
Actor and private memory, and takes effect in the next scene. No user
confirmation endpoint or promotion proposal store exists.

The branch-local Active Agent Pool has no fixed size limit. At every completed
scene boundary, the Game Master selects between one and four Active Agents for
the next `roster_actor_ids`. Each simulation step selects one Acting Agent from
that Scene Roster. The runtime does not automatically retire or replace an
active Character. Structured participant IDs must resolve to a registered
Character, and observation owners must resolve to active Actors.

## Consequences

### Positive

- Ordinary people do not consume model calls.
- One Character identity survives checkpoint, branch, Wiki, and UI recovery.
- Autonomous simulation no longer pauses for promotion confirmation.
- Large casts can retain agency without making every Agent participate in every
  scene.
- Promotion decisions are evidence-backed, auditable, and reversible through
  checkpoint rollback.

### Negative

- An Editor mistake can add an unnecessary Actor before the user notices.
- A Game Master roster decision may omit an Agent that would have been useful
  in the next scene.
- Existing checkpoints and clients using dynamic entity fields are not
  compatible with the new contract.

### Neutral

- User confirmation still applies to Story Events; this decision changes only
  NPC promotion.

## Alternatives Considered

Keeping manual confirmation was rejected because it interrupts autonomous
simulation. Immediately turning every new NPC into an Actor was rejected
because it contradicts the minimal-Agent product rule and allows uncontrolled
Agent creation. Capping the entire Active Agent Pool was rejected because cast
size and scene participation are separate concerns; the Scene Roster limit
controls runtime breadth directly. Keeping both persistent NPCs and dynamic
entities was rejected because the duplicate lifecycle caused inconsistent
identity and knowledge boundaries.

## References

- the former AI Story Evolution Engine product plan (available in Git history)
- the current [Living Story World PRD](../product-plan.md)
- [ADR-0009](0009-living-story-world-product-boundary.md)
- `docs/architecture.md`
