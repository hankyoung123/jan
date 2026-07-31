# Contributing to Story Engine

Story Engine is a Jan-based desktop application with a Python story-domain
Sidecar. Read the canonical requirements before changing code:

- [`docs/product-plan.md`](docs/product-plan.md)
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/domain-model.md`](docs/domain-model.md)
- [`docs/data-contracts.md`](docs/data-contracts.md)
- relevant records under [`docs/adr/`](docs/adr/)

## Architecture boundary

Keep the inherited Jan desktop and model infrastructure authoritative:

- `web-app/` owns the React shell, themes, settings, Provider screens, and
  model center.
- `src-tauri/`, `core/`, and retained extensions own desktop integration,
  downloads, Provider secrets, and local llama.cpp/MLX runtimes.
- `apps/story-engine/` owns story-domain orchestration, canonical Markdown,
  review, retrieval authorization, and task profiles.

Do not introduce another desktop shell, Provider registry, keychain namespace,
or inference runtime. React must not write canonical project files. Model
output must pass schema validation, review, and explicit user approval before
`EventCommitService` changes formal Markdown.

## Development setup

```bash
corepack enable
yarn install
yarn bootstrap:jan
uv sync --project apps/story-engine --extra dev
```

Run the Python Sidecar and web application in separate terminals:

```bash
yarn story-engine:dev
yarn dev:web
```

## Change workflow

Each change should have one measurable objective and follow the repository's
contract-first sequence:

```text
contract -> failing test -> minimal implementation -> verification -> docs
```

Before submitting a change, run the gates relevant to its scope. The complete
local gate is:

```bash
yarn lint
yarn typecheck
yarn test
yarn contracts:check
yarn build
```

Python-only changes must also pass Ruff, mypy, pytest, and package build.
Desktop changes must pass Cargo formatting, Clippy, and an appropriate Tauri
build smoke test.

## Upstream and licensing

Jan-derived files retain their upstream license and attribution. Product code
must not reuse Jan trademarks, logos, screenshots, or marketing assets. Do not
copy source with unclear or incompatible licensing. Update
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), [`licenses/`](licenses/),
and the migration map when retained upstream areas or bundled dependencies
change.
