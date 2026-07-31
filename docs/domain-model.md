# Domain Model

## Aggregates

`Project` owns identity and creative direction. `WorldState` owns current time,
location, pressures, public facts, variables, and an optimistic version.
`Character` owns its private knowledge boundary, goal, relationships, resources,
location, state, and optimistic version.

`TurnCandidate` is explicitly non-canonical. It records the base world and
character versions, isolated character intents, a resolver outcome, one review,
and lifecycle status.

`StoryEvent` is the immutable canonical record produced from an approved
candidate. Corrections are new amendment events; existing event files never
change.

## Invariants

- Active characters have a current goal.
- Character intents express intended action, never successful outcome.
- Resolver outcomes may see all current intents; characters may not.
- Character context includes only authorized facts and experienced events.
- A candidate cannot be approved without a passing current review.
- Editing a reviewed candidate clears its review and returns it to draft.
- Commit requires matching base versions for the world and every participant.
- Only an approved candidate can be committed.
- Event sequence numbers are monotonically increasing and append-only.
- Writer inputs contain confirmed events and authorized style/world context only.

## Core value types

- `CharacterIntent`
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

