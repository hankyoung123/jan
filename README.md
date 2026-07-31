# AI Story Evolution Engine Next

AI Story Evolution Engine Next is a local-first desktop system for evolving
long-form stories from character-limited knowledge, independent intent, world
resolution, editorial review, and explicit user approval.

The repository is under active construction. The canonical product
requirements are in [docs/product-plan.md](docs/product-plan.md).

## Locked upstreams

- Jan `v0.8.4` at `5f30aee467f08941964a83f946e2663e7ae0e01f`
- Concordia `v2.4.0` at `702998f57da71f87bf4e607abc1325ee51cca21f`

Jan is registered as the `upstream` Git remote. Product code is developed in
this repository; upstream code is only migrated with a directory-level license
review and an accompanying notice.

## Planned workspace

```text
apps/desktop       React + Tauri desktop application
apps/story-engine  FastAPI story engine sidecar
packages/contracts Shared OpenAPI and generated TypeScript contracts
packages/ui        Reusable product UI
packages/editor    Tiptap-based manuscript editor
```

## Prerequisites

- Node.js 20 or newer
- pnpm 10
- Rust 1.80 or newer
- uv
- Python 3.12

Development and verification commands will be added with the first runnable
vertical slice.

