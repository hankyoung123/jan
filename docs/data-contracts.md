# Data contracts

## Runtime files

Each project stores simulation infrastructure below `.story-engine/runtime`:

```text
runtime/
├── branches/<branch-id>.json
├── checkpoints/checkpoint-<sha256>.json
└── logs/<branch-id>.jsonl
```

Branch manifests contain project and branch IDs, parent/fork metadata, head
checkpoint, head step, locale, and timestamps. Checkpoint envelopes contain a
schema version, content-addressed checkpoint ID, and one complete
`TurnSessionSnapshot`. Log records contain the matching checkpoint/state hash,
`StepResult`, and `TurnTrace`.

All persisted models reject unknown fields. Datetimes are timezone-aware.
Checkpoint load recalculates the canonical JSON hash (excluding only
`state_hash` and `checkpoint_id`) and rejects tampering.

`TurnSessionSnapshot.characters` contains the complete branch-local character
projection. `roster_actor_ids` contains only the current Scene Roster and is
limited to four Active Agent IDs.

## Commit semantics

Step identity is `(session_id, step)`. Re-appending the identical log record is
idempotent; conflicting duplicates and non-increasing step sequences are
rejected. Branch head updates use an expected-head compare-and-swap and reject
concurrent writers.

## Wiki files

`wiki/branches/<branch-id>/` pages and their version snapshots are written as
one recoverable `AtomicBatch`. They include only history at or before the
selected checkpoint boundary. Branch-local log records provide step traces,
while the checkpoint's Game Master memory supplies inherited world events after
a fork. Removing Wiki pages does not alter branch manifests, checkpoints, or
logs.

## HTTP and TypeScript

FastAPI generates `packages/contracts/openapi.json`; `openapi-typescript`
generates `packages/contracts/src/generated.ts`. Run `yarn contracts:generate`
after changing a public model or route. CI must fail when generated contracts
drift from the application schema.

The production control surface is session-based:

- `/projects/{project_id}/simulations` and session control subroutes;
- `/projects/{project_id}/branches` for forks;
- branch rollback and Wiki rebuild routes.

WebSocket envelopes use `subject_id` and the `simulation.*` event family.
