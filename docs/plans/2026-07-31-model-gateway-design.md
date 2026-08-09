# Model Gateway and Profile Registry Design

> **Historical infrastructure design.** Reusable model-gateway boundaries
> remain relevant, but old Writer/Editor task profiles do not define current
> MVP scope. The Living Story World PRD and ADR-0009 take precedence.

> Superseded in part by
> [ADR-0004](../adr/0004-jan-model-runtime-bridge.md). Jan, rather than Python,
> is the sole Provider and inference authority.

## Decision

The application uses Jan's model center and runtime as the sole Provider/model
implementation. Python owns only the Story Engine task-profile registry and
one OpenAI-compatible transport to a private Jan loopback proxy. This keeps
task routing, limits, structured-output validation, and usage accounting in
the domain process without duplicating Jan's Provider or secret handling.

Provider-specific SDKs were rejected for V1 because they duplicate retry and
validation behavior and make local/remote interchange harder. Browser-direct
calls were rejected because they expose credentials and split the registry
between React and Python.

## Storage and security

Task profile configuration is stored atomically in the application data
directory, outside every story project. Provider configuration and credentials
remain in Jan and its operating-system secret store. Python stores no Provider
URL or credential. Its only model target is a random loopback proxy URL and
process-local bearer token injected by the desktop runtime.

Five stable task profiles are always available: Character, Resolver, Editor,
Writer, and Embedding. A profile selects its provider and model and owns the
default temperature, timeout, and output-token limit. Project configuration
will reference profile IDs and may supply narrower task overrides.

## Invocation flow

The gateway resolves a profile, clamps the request to system limits, and sends
an OpenAI-compatible request to the private Jan proxy. Jan selects the remote
Provider or local llama.cpp/MLX session and injects Provider credentials. The
gateway retries transient bridge failures. Responses are read
with a hard byte ceiling before JSON parsing. Structured requests parse the
assistant content as JSON and validate it against the supplied JSON Schema.
Provider failures, timeouts, limit violations, and parse failures use stable
error codes. Usage is returned per call and accumulated in process memory.

Streaming uses server-sent events from the same provider endpoint. The gateway
forwards text deltas while retaining enough content to apply the same final
size and structured-output checks. Cancellation is native task cancellation:
cancelling the caller closes the upstream HTTP stream.

## Product surface

Jan's retained model center remains the Provider, model-download, and local
runtime console. Story Engine adds a task-profile surface for the five domain
routes. Provider CRUD is not exposed by the Python API. Secrets never appear in
project files, browser persistence, Sidecar arguments, status events, or logs.

The task-profile surface is implemented as a collapsible operational band in
the retained Model Center. It reads Provider and model options from Jan state,
edits the five generated `ModelProfile` contracts, exposes aggregate usage, and
links to Jan Provider settings. The form never accepts or renders credentials.

## Evolution integration

The FastAPI app constructs one `ModelGateway` and injects it into both model
routes and `ConcordiaStoryAdapter`. `EvolutionService` sends one authorized
`CharacterContext` to each Concordia entity concurrently, then gives the
completed intents and current world to one Concordia Game Master for unified
resolution. The previous fog-harbor-specific production generator has been
removed.

Concordia exposes a synchronous entity interface, so the project route runs a
turn in a worker thread. Character calls use their own worker pool inside that
turn. The gateway registry, transport, and usage accounting remain shared;
there is still only one Jan Provider/runtime authority.

## Verification

Unit tests cover the React profile load/edit/retry flow, secret-free request
bodies, atomic profile persistence and v1 migration, all five profile types,
the absence of Python Provider endpoints, bridge authentication,
remote/local profile interchange, retries, response limits, structured-output
parse and schema failures, streaming, and usage accounting. Rust tests cover
random-port binding, environment-only bridge credentials, and lifecycle
cleanup. OpenAPI generation, Python static checks, frontend tests, and desktop
runtime checks remain release gates.

Evolution integration tests additionally prove two isolated Character calls,
one later Resolver call, parallel Character execution, stable failure when the
Tauri bridge is unavailable, and byte-identical canonical Markdown after that
failure.
