# Domain Model

## Aggregates

`Project` owns identity and creative direction. `WorldState` owns current time,
location, pressures, public facts, variables, and an optimistic version.
`Character` owns its private knowledge boundary, goal, relationships, resources,
location, state, and optimistic version.

`TurnCandidate` is explicitly non-canonical. It records the base world and
character versions, isolated character intents, a resolver outcome, one review,
and lifecycle status.

`NpcCandidate` is a minimal, non-canonical Resolver proposal. It carries a
filesystem-safe identifier, identity, purpose, and optional goal. It does not
become a `Character` until the containing Turn is approved by the user and
committed. The committed Character has type `npc` and remains outside active
Character Agent contexts.

`PromotionCandidate` is a derived Editor recommendation tied to one project,
one NPC, and the NPC's current version. It carries a proposed active goal, a
passing `promotion_review`, and a `pending` or `committed` lifecycle. It is not
Canon and cannot move a Character by itself.

`StoryEvent` is the immutable canonical record produced from an approved
candidate. Corrections are new amendment events; existing event files never
change.

## Invariants

- Active characters have a current goal.
- Character intents express intended action, never successful outcome.
- Resolver outcomes may see all current intents; characters may not.
- Resolver outcomes must reuse an existing Character when one can plausibly
  serve the required role before proposing a new NPC.
- NPC candidate identifiers are unique within an outcome and cannot reuse an
  existing Character identifier.
- An NPC candidate is canonical only after user confirmation creates its
  Markdown in the Event commit batch.
- NPC Characters are not Character Agents unless a user explicitly promotes
  them to active status.
- Promotion requires a passing derived recommendation plus explicit user
  confirmation of the matching candidate ID.
- Promotion commit rechecks the NPC version and atomically moves its Markdown
  from `characters/npc` to `characters/active` while appending an approval
  Event.
- Character context includes only authorized facts and experienced events.
- A candidate cannot be approved without a passing current review.
- Editing a reviewed candidate clears its review and returns it to draft.
- Revision preserves Character intents, regenerates the Resolver outcome, and
  requires a new Editor review.
- A generated candidate snapshots every existing Character version. Commit
  requires matching base versions for the world and every participating or
  modified Character.
- Only an approved candidate can be committed.
- Event sequence numbers are monotonically increasing and append-only.
- Writer inputs contain confirmed events and authorized style/world context only.

## Core value types

- `CharacterIntent`
- `NpcCandidate`
- `PromotionCandidate`
- `WorldOutcome`
- `ReviewResult`
- `StateChange`
- `Relationship`
- `RetrievalEvidence`
- `ModelRequest`
- `ModelProfile`

Pydantic schemas are the executable contract for Python. OpenAPI is generated
from the running API, and TypeScript types are generated from the committed
OpenAPI document.
