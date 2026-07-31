# Architecture

## System boundary

The product has three runtime boundaries:

1. React renders views and collects user decisions. It never writes project
   Markdown or commits domain state.
2. Tauri owns the desktop lifecycle, starts the Python sidecar, protects its
   session token, and exposes operating-system capabilities.
3. The FastAPI story engine owns all story-domain behavior, model calls,
   retrieval, validation, review, and persistence.

React communicates with the story engine over loopback HTTP and WebSocket.
Every request except the unauthenticated liveness probe must include the
per-process session token. Production builds package Python as an onedir
sidecar.

## Source of truth

Canonical project state is Markdown:

```text
project.md
world.md
characters/
events/
scenes/
```

Derived state lives below `.story-engine/` and must be rebuildable. Models,
retrieval indexes, caches, UI stores, and Concordia objects are never canonical
story state.

## Write path

All formal mutations flow through `EventCommitService`:

```text
candidate -> schema validation -> editor review -> user approval
          -> optimistic version check -> atomic Markdown writes
          -> event append -> index refresh
```

An unapproved turn may write only to `.story-engine/turns` and
`.story-engine/reviews`.

`SubmissionService` validates the runnable initial package before creating a
project directory. During evolution, `CharacterContextAssembler` combines
public world facts with only the selected character's private fact IDs.
`EvolutionService` generates each intent from one such context, performs one
unified resolution, and obtains an Editor review before exposing a candidate
to React. Other characters' private facts and current-turn intents are absent
from every character context.

## Dependency direction

```text
api -> application services -> domain
                            -> workspace ports
                            -> model/retrieval ports
infrastructure adapters ----^
```

The domain package has no FastAPI, filesystem, Concordia, or provider imports.
Concordia remains behind `concordia_adapter` and only returns candidate intent
or outcome values.

## Failure handling

- Invalid or missing tokens return `401` without leaking configuration.
- Provider errors are normalized before crossing the API boundary.
- Candidate edits invalidate existing review state.
- Version conflicts return `409` and never partially write canonical files.
- Atomic writes use a sibling temporary file, flush, `fsync`, and `os.replace`.
- Sidecar startup is gated by `/health`; crashes surface a restart action.

## Desktop Sidecar lifecycle

Tauri reserves a loopback port, generates a process-local 64-character token,
and injects it into the Sidecar environment. The token is absent from command
line arguments, project files, status events, and captured logs. React obtains
the current base URL and token through a Tauri command and retains neither in
persistent browser storage.

The desktop runtime retains the last 200 redacted log lines, polls `/health`
before declaring the engine ready, monitors the child process, and emits an
immediate status event on startup, readiness, stop, or crash. The UI exposes a
restart action for stopped or crashed states. Development uses `uv`; packaged
builds resolve the onedir Sidecar from application resources.

See [ADR-0001](adr/0001-platform-and-upstream-locks.md) and
[ADR-0002](adr/0002-markdown-canonical-state.md).
