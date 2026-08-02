# ADR-0002: Markdown is canonical state

- Status: Partially superseded by ADR-0005
- Date: 2026-07-31

## Decision

Store project seeds, human-authored inputs, editable projections, and manuscript
exports in human-readable Markdown with validated front matter. Keep active
simulation branch state, checkpoints, logs, reviews, caches, and indexes under
`.story-engine/`.

For legacy event workflows, `EventCommitService` remains the mutation boundary
until their replacement is complete. ADR-0005 makes a branch checkpoint plus
its Game Master memory, private actor memories, component state, and raw log the
canonical state of a running simulation. Markdown becomes a rebuildable
projection at that boundary.

## Consequences

Indexes must be rebuildable from their declared source. Simulation projections
must rebuild from checkpoints and append-only logs; project seeds and manual
edits remain independently human-readable. Model calls and UI state cannot
directly advance a simulation branch head. Multi-file commit recovery metadata
is required before production packaging.

Project open is the recovery boundary: prepared transactions are rolled back
before canonical documents are validated. Each open project has one disposable
in-memory index and one file watcher; closing it drops both without touching
Markdown. API commits refresh the index synchronously, while external edits
are detected by the watcher and surfaced through authenticated workspace
events. The original atomic-write, recovery, and human-readable export goals
remain accepted; the claim that every simulation step must first become an
approved Markdown event is superseded by ADR-0005.

## Superseding decision

See [ADR-0005](0005-concordia-native-runtime.md) for the persistent Concordia
runtime, branch/checkpoint authority, and removal of mandatory per-event user
approval.
