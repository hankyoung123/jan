# Data Contracts

The [Living Story World PRD](product-plan.md) defines semantic authority.
Serialization is an implementation detail behind validated stores.

## Authority matrix

| Data | Role | May directly change World Truth? |
| --- | --- | --- |
| Validated ResolvedEvent | Committed history | Yes, through validated effects |
| Checkpoint and Branch head | Recoverable state and lineage | Yes, as the committed result |
| Actor State and Memory | Recoverable runtime state | Only through Resolution |
| User/NPC input and Intent | Putative attempt | No |
| Belief and Perception | Actor-scoped state/projection | No |
| Wiki, Narrative, UI Scene, Summary, Manuscript | Rebuildable projection | No |
| Model trace/reasoning and Provider cache | Audit/performance data | No |

## Runtime persistence

The current implementation stores simulation infrastructure below
.story-engine/runtime:

    runtime/
    ├── branches/<branch-id>.json
    ├── checkpoints/checkpoint-<sha256>.json
    └── logs/<branch-id>.jsonl

Branch manifests contain project/branch identity, parent and fork metadata,
head checkpoint, head step, locale, and timestamps. A checkpoint envelope
contains a schema version, a content-addressed checkpoint ID, and a complete
TurnSessionSnapshot. Log records bind StepResult and TurnTrace to the matching
checkpoint/state hash.

All persisted models reject unknown fields. Datetimes are timezone-aware.
Checkpoint load recalculates the canonical hash and rejects tampering.
TurnSessionSnapshot.characters contains the branch-local Actor projection;
runtime roster fields are scheduling details, not a separate product concept.

The current JSON/JSONL encoding is not product-level authority and may be
replaced without changing the World protocol. No UI or model output may bypass
the stores by writing these files directly.

## Commit semantics

Step identity is (session_id, step). Re-appending an identical record is
idempotent; conflicting duplicates and non-increasing step sequences are
rejected. Branch-head updates use an expected-head compare-and-swap.

The safe order is:

1. Validate Resolution and state effects.
2. Write and verify the immutable checkpoint.
3. Persist the matching ResolvedEvent result and trace.
4. Atomically advance the branch manifest head.

Input, narrative, belief, perception, or a failed model call cannot advance the
head. A failed projection update cannot roll back a valid world commit or
become a prerequisite for restoring it.

An explicit player belief is represented by a deterministic Actor-state effect
with the putative action ID as its source. It commits atomically with the turn,
survives checkpoint restore, and never changes the matching World Fact.

## Resolution context

`ResolverContext` is a bounded request projection, not another state owner. It
contains the current Intent, affected Actor State, relevant World facts, and a
small recent causal window. It excludes pacing pressure, clocks, and the full
world-variable map.
Canonical facts are filtered for relevance; the complete World Truth is never
serialized into every Resolution call.

World Truth, Actor Knowledge, and Actor Belief remain separate fields and
authorities. Wiki text is non-authoritative and may be missing without weakening
canonical constraints. Only the Game Master receives hidden canonical facts;
Actor prompts and player-facing responses do not.

`InitiativeContext` is the separate world-only view for the same Game Master.
It contains pressures, clocks, relevant World state, and recent causal events.
Neither context grants the GM authority over an Actor's beliefs, current goal,
intent, voluntary dialogue, or voluntary action.

`ResolutionStateUpdate` validates exact target/path/value combinations while
parsing model output. Character updates support physical location, conditions,
and resources; World updates support current time and location. Arbitrary
`JsonValue` mutation paths are not accepted. Runtime applies already-typed
effects to a candidate, checks cross-state invariants such as references,
resource legality, and monotonic time, and commits atomically.

## Projection files

Branch Wiki pages and manuscript drafts may remain human-readable Markdown.
They must name their source checkpoint or event lineage when that distinction
matters. Removing or rebuilding a projection does not alter branch manifests,
checkpoints, events, Actor state, or Memory.

Project-authored Markdown can provide initialization seeds. After the world is
initialized, changing an authoring document does not retroactively change
committed history and cannot rewrite the frozen Truth Seed.

## HTTP and TypeScript

FastAPI generates packages/contracts/openapi.json; openapi-typescript generates
packages/contracts/src/generated.ts. Run yarn contracts:generate after changing
a public model or route. CI must fail when generated contracts drift.

The interactive world command is:

    POST /projects/{project_id}/simulation/turn
    {
      "text": "我走过去看看桌上的东西",
      "command_id": "interactive:550e8400-e29b-41d4-a716-446655440000"
    }

`command_id` is generated by the client and identifies the whole UI turn. The
runtime derives separate `:player`, `:npc`, and when triggered `:initiative`
child commands. Each child has an
independent durable receipt and checkpoint, so replay does not duplicate an
already committed player action and an NPC failure restores to the player
checkpoint.

The NPC child always completes before initiative is considered. Initiative is
blocked while response Actor IDs are pending and uses the same ResolvedEvent,
checkpoint, log, and branch-head commit path as Actor turns.

The client generates `command_id` once per fresh intent. If the network outcome
is unknown, it first refreshes the restricted session projection and offers a
retry with the original ID and text. A genuine retry never generates a new ID;
an explicit HTTP rejection is treated as definitive and discards that retry.

The response is restricted to the requesting Actor's perception, visible
events, own state, checkpoint identity, and world time. It must not serialize a
complete TurnSessionSnapshot, another Actor's private Memory, GM reasoning,
hidden facts, undiscovered evidence, or raw Concordia state.

`POST /projects/{project_id}/simulation/session` explicitly creates or opens an
interactive runtime. `GET /projects/{project_id}/simulation/session` is a
strictly read-only projection of the current committed branch head: it never
creates a world/session, restores a runtime, invokes a model, advances a step,
commits, or creates a checkpoint.

An NPC handoff left pending after an interrupted turn is completed only by
`POST /projects/{project_id}/simulation/recovery` with a caller-generated
`command_id`. Recovery derives the durable NPC receipt as `{command_id}:npc`;
checkpoint IDs locate state and are never used as command identity. Retrying
the same recovery command returns its committed result without executing the
NPC twice.

Both session routes return the same restricted projection for the World
Session. Branch and checkpoint routes provide Timeline and “从这里继续”
behavior without exposing a parallel save-game store. Reopened scene prose is
reconstructed from the selected checkpoint lineage, latest Scene Boundary,
subsequent visible ResolvedEvents, and current location/time; it is not copied
from the original scene forever.

WebSocket events are delivery hints for committed session changes. HTTP
snapshots and durable branch state remain authoritative when an event is
missed.
