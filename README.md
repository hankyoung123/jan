# AI Story Evolution Engine Next

AI Story Evolution Engine Next is a local-first desktop system for evolving
long-form stories from character-limited knowledge, independent intent, world
resolution, editorial review, and explicit user approval.

The canonical requirements are in [docs/product-plan.md](docs/product-plan.md).

## Upstream foundation

This branch descends directly from Jan `v0.8.4` at
`5f30aee467f08941964a83f946e2663e7ae0e01f`. Jan supplies the Tauri shell,
React infrastructure, settings, Provider management, model acquisition, and
local llama.cpp/MLX runtimes. Product branding and the general chat domain are
being replaced while the reusable local-model infrastructure remains intact.

Concordia is locked to `v2.4.0` at
`702998f57da71f87bf4e607abc1325ee51cca21f` and will be consumed as an
unmodified Python dependency behind `concordia_adapter`.

## Workspace

```text
web-app/             Jan-based authoritative React application
src-tauri/            Jan-based authoritative Tauri runtime and plugins
core/                 Jan model and extension contracts
extensions/           Jan model download and local inference extensions
apps/story-engine/    Python story-domain Sidecar
packages/contracts/   Generated Story Engine OpenAPI/TypeScript contracts
```

## Prerequisites

- Node.js 20 or newer
- Yarn 4.5.3 through Corepack
- Rust 1.80 or newer
- Make 3.81 or newer
- uv and Python 3.12
- macOS Apple Silicon builds: Metal Toolchain

## Development

Install the Jan workspace and Python environment:

```bash
corepack enable
yarn install
yarn bootstrap:jan
uv sync --project apps/story-engine --extra dev
```

Start the Story Engine and Jan-based web application in separate terminals:

```bash
yarn story-engine:dev
yarn dev:web
```

For the native application, use `yarn dev:tauri` after the platform-specific
Jan prerequisites and local inference binaries are available.

## Quality gates

```bash
yarn lint
yarn typecheck
yarn test
yarn contracts:check
yarn build
```

## Attribution

Jan-derived source remains under the Apache License 2.0 with upstream notices
preserved. Jan names, logos, screenshots, and other trademark assets are not
part of the product identity. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)
and [the migration map](docs/upstream/jan-v0.8.4-migration-map.md).
