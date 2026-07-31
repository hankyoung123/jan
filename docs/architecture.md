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

Inside the Python boundary, unmodified Concordia 2.4.0 is the character and
Game Master engine. It is a library used by the Story Engine, not a second
desktop process or model runtime. The concrete stack is therefore Tauri shell,
React UI, Python Story Engine, and original Concordia.

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
from every character context. Character entities are invoked concurrently;
the Concordia Game Master runs only after all selected intents complete.

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

The adapter builds original Concordia entities from one authorized
`CharacterContext` at a time and a Concordia Game Master from the world plus
all completed intents. Concordia's language-model interface is backed only by
the existing Python `ModelGateway`, which reaches Jan's private runtime bridge;
the adapter contains no Provider SDK, credential store, or canonical write
path.

FastAPI injects its single process-level `ModelGateway` into the Concordia
adapter. Since Concordia's entity API is synchronous, turn generation runs in
a worker thread rather than blocking the API event loop. A missing Tauri model
bridge fails before a candidate is saved and leaves canonical Markdown
byte-identical.

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

## Model boundary

Jan owns model discovery, download, loading, Provider configuration UI, and the
local llama.cpp process. Domain calls cross into Python through `ModelGateway`,
which resolves Character, Resolver, Editor, Writer, and Embedding profiles from
one application-level registry. Every profile invokes a private Jan
OpenAI-compatible proxy, which routes the selected model to a remote Provider,
llama.cpp, or MLX. Python does not implement a second Provider stack.

Task profile data is written atomically under application data, not inside
story projects. Jan owns Provider URLs and operating-system keychain entries.
The desktop passes only a random loopback proxy URL and process-local bearer
token to the Sidecar through environment variables; neither appears in status
events or command-line arguments.

The gateway enforces request and response byte ceilings, timeout and output
token limits, transient retries, stable provider errors, per-call and aggregate
usage accounting, and final JSON Schema validation for structured responses.

See [ADR-0001](adr/0001-platform-and-upstream-locks.md),
[ADR-0002](adr/0002-markdown-canonical-state.md), and
[ADR-0004](adr/0004-jan-model-runtime-bridge.md).
