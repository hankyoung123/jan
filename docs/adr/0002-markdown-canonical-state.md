# ADR-0002: Markdown is canonical state

- Status: Accepted
- Date: 2026-07-31

## Decision

Store all formal project, world, character, event, and manuscript state in
human-readable Markdown with validated front matter. Keep runtime candidates,
reviews, caches, and indexes under `.story-engine/`.

`EventCommitService` is the sole canonical mutation boundary. It performs
optimistic version validation and atomic filesystem replacement before
refreshing derived indexes.

## Consequences

Indexes must rebuild from Markdown. Event records are append-only. Candidate
generation, model calls, and UI state cannot directly mutate canonical files.
Multi-file commit recovery metadata is required before production packaging.

Project open is the recovery boundary: prepared transactions are rolled back
before canonical documents are validated. Each open project has one disposable
in-memory index and one file watcher; closing it drops both without touching
Markdown. API commits refresh the index synchronously, while external edits
are detected by the watcher and surfaced through authenticated workspace
events.
