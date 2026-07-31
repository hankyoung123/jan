# Model Gateway and Profile Registry Design

## Decision

The application uses one Python-owned model registry and one
OpenAI-compatible transport for both remote providers and the local Jan or
llama.cpp endpoint. React never calls providers directly. This keeps task
routing, limits, structured-output validation, usage accounting, and secret
handling in one process.

Provider-specific SDKs were rejected for V1 because they duplicate retry and
validation behavior and make local/remote interchange harder. Browser-direct
calls were rejected because they expose credentials and split the registry
between React and Python.

## Storage and security

Non-sensitive provider and profile configuration is stored atomically in the
application data directory, outside every story project. Provider credentials
are addressed by provider ID and stored through the operating-system keychain.
API responses expose only a `has_api_key` boolean. Local provider URLs must use
loopback HTTP or HTTPS; remote provider URLs must use HTTPS.

Five stable task profiles are always available: Character, Resolver, Editor,
Writer, and Embedding. A profile selects its provider and model and owns the
default temperature, timeout, and output-token limit. Project configuration
will reference profile IDs and may supply narrower task overrides.

## Invocation flow

The gateway resolves a profile, clamps the request to system limits, retrieves
the credential inside Python, and sends an OpenAI-compatible request. It
retries transient network, rate-limit, and server failures. Responses are read
with a hard byte ceiling before JSON parsing. Structured requests parse the
assistant content as JSON and validate it against the supplied JSON Schema.
Provider failures, timeouts, limit violations, and parse failures use stable
error codes. Usage is returned per call and accumulated in process memory.

Streaming uses server-sent events from the same provider endpoint. The gateway
forwards text deltas while retaining enough content to apply the same final
size and structured-output checks. Cancellation is native task cancellation:
cancelling the caller closes the upstream HTTP stream.

## Product surface

The model center is a restrained operational console consistent with the
existing desktop shell. It lists the five task routes, distinguishes local and
remote providers, edits endpoint/model/limits, accepts write-only API keys,
and runs a small connectivity test. Secrets never appear in persistent browser
storage, URLs, status events, or logs.

## Verification

Unit tests cover atomic registry persistence, all five profile types, endpoint
security rules, secret-safe API responses, remote/local interchange, retries,
response limits, structured-output parse and schema failures, streaming, and
usage accounting. OpenAPI generation, Python static checks, frontend tests,
and desktop visual checks remain release gates.
