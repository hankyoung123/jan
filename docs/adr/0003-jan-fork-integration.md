# ADR-0003: Jan fork integration

- Status: Accepted
- Date: 2026-07-31
- Supersedes: ADR-0001 workspace strategy only

> ADR-0009 amends the product boundary in this record. Jan remains the desktop
> and model foundation, while Writer, Editor, Markdown authoring, review, and
> retrieval code are legacy or future projection capabilities rather than
> World Session core dependencies.

## Context

The product plan requires Jan to be the desktop foundation, including its
Tauri shell, React infrastructure, settings, Provider management, model
download, local inference, updater, and packaging flows. The initial clean
monorepo implemented a separate desktop shell and kept Jan only as a remote
reference. That approach did not satisfy the upstream requirement and created
duplicate model and lifecycle infrastructure.

## Decision

The integration branch descends directly from Jan `v0.8.4` commit
`5f30aee467f08941964a83f946e2663e7ae0e01f` and merges the Story Engine product
history. These Jan directories remain the authoritative desktop baseline:

- `web-app` for React, themes, settings, Provider and model interfaces;
- `core` for extension, engine and model contracts;
- `extensions/download-extension`, `llamacpp-extension`, and `mlx-extension`;
- `src-tauri` and its llama.cpp, MLX, hardware, updater and packaging plugins.

The Jan Yarn 4 workspace remains the root JavaScript build system during the
integration. Python continues under `apps/story-engine`, and generated API
contracts remain under `packages/contracts`.

The pre-integration `apps/desktop` tree was migration input only. Its
story-specific routes and Python Sidecar lifecycle were ported into `web-app`
and the root `src-tauri`; the independent shell, model-center placeholder, and
duplicate Tauri runtime were then removed.

## Boundaries

Jan continues to own model acquisition, loading, Provider configuration UI,
Provider protocol handling, secrets, and local inference processes. Python
owns story-domain orchestration, Markdown, ModelGateway task profiles,
Concordia adaptation, review, Writer, and retrieval authorization. Model calls
cross the private runtime bridge defined by ADR-0004. The AGPL RAG
implementation is not imported into the closed product.

## Consequences

The repository retains Jan's original commit ancestry and full Apache-2.0
license. Product commits must update modification notices for rebranded or
removed Jan areas. Jan trademark assets are removed from distributed builds,
but attribution and license texts remain. Upstream updates are evaluated as
real Git changes rather than manually reimplemented behavior.
