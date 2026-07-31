# ADR-0004: Route Story Engine model calls through the Jan runtime

## Status

Accepted

## Context

The product keeps Jan's model center, Provider management, downloads, local
llama.cpp/MLX runtime, and OpenAI-compatible proxy. The first Story Engine
prototype nevertheless added a second Python Provider registry, a second
keychain namespace, and direct remote Provider HTTP calls. That duplicated
Jan's responsibilities and allowed the two configuration surfaces to drift.

Story-domain calls still need Python-owned task profiles, request limits,
structured-output validation, retries, cancellation, and usage accounting.
Provider secrets must not enter project files, browser persistence, Sidecar
arguments, status events, or logs.

## Decision

Jan is the sole model infrastructure and Provider authority. The desktop starts
a private Jan OpenAI-compatible proxy on a random loopback port with a strong
process-local bearer token. The proxy shares Jan's live Provider configuration,
keychain-backed secrets, llama.cpp router, and MLX sessions.

The desktop passes the proxy URL and bearer token to the Python Sidecar through
environment variables. Python's `ModelGateway` sends every remote and local
request to that one private endpoint. Python retains only Story Engine task
profiles and domain-level execution policy; it no longer stores Provider URLs
or Provider credentials and does not implement Provider-specific protocols.

```text
Jan model center / Provider settings
                 |
                 v
Jan runtime + keychain + llama.cpp / MLX
                 |
        private loopback proxy
                 |
                 v
Python ModelGateway task profiles
                 |
                 v
Story workflows and structured validation
```

The bridge is never exposed in the webview runtime state. Its token is absent
from command-line arguments and redacted from captured Sidecar logs. Stopping
or exiting the desktop terminates both the Sidecar and private proxy.

## Non-functional requirements

- Bind only to `127.0.0.1` and reject unauthenticated model requests.
- Allocate the port dynamically so parallel installations do not conflict.
- Keep Provider keys inside Jan's Rust process and operating-system secret
  storage; Python receives only the private proxy token.
- Preserve streaming and cancellation without buffering complete responses.
- Treat bridge startup failure as an explicit crashed Sidecar state.
- Keep the user-configurable Jan Local API Server independent from the private
  bridge.

## Consequences

### Positive

- There is one Provider/model implementation instead of parallel Jan and
  Python implementations.
- Remote Provider converters, fallback keys, downloads, and local runtime
  behavior remain inherited from Jan.
- Story task policies remain testable in Python without exposing credentials.
- Local and remote models use the same OpenAI-compatible boundary.

### Negative

- The Sidecar now depends on the desktop-owned proxy being ready.
- Model profile configuration must use model IDs known to Jan.
- Packaged Sidecar tests need an injected mock bridge endpoint.

### Neutral

- The public Story Engine API continues to expose task profile operations and
  usage, but Provider CRUD belongs only to Jan's settings UI.

## Alternatives considered

**Keep Python's direct remote Provider transport and synchronize Jan settings**

Rejected because it preserves duplicate protocol, retry, keychain, and routing
implementations and can drift from Jan.

**Let React forward Provider credentials to Python at runtime**

Rejected because it exposes complete secrets to the webview and violates the
product security boundary.

**Reuse Jan's user-configurable Local API Server**

Rejected because it can be disabled, rebound to a LAN interface, or configured
with a user-selected port/token. Story execution needs a private, invariant
loopback channel.

## References

- `docs/product-plan.md`, sections 3.2, 3.4, and 9
- `docs/adr/0003-jan-fork-integration.md`
- `docs/upstream/jan-v0.8.4-migration-map.md`
