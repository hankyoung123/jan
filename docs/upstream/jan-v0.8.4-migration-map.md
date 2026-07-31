# Jan v0.8.4 Migration Map

- Upstream: `https://github.com/janhq/jan.git`
- Tag: `v0.8.4`
- Commit: `5f30aee467f08941964a83f946e2663e7ae0e01f`
- License: Apache-2.0

## Required source baseline

The Jan integration branch must descend from the locked commit and retain the
following dependency chains as source, not as visual references:

| Jan area | Product ownership | Migration rule |
|---|---|---|
| `web-app` | Desktop shell, theme, settings and model UI | Keep and rebrand; replace chat routes with story routes while retaining reusable infrastructure |
| `core` | Model entities, extension contracts and engine management | Keep; expose stable adapters to Story Engine profile configuration |
| `extensions/download-extension` | Model acquisition | Keep and rebrand |
| `extensions/llamacpp-extension` | Local llama.cpp backend lifecycle | Keep; Python calls its loopback OpenAI-compatible endpoint |
| `extensions/mlx-extension` | Apple Silicon local inference | Keep where platform support remains enabled |
| `src-tauri` | Desktop lifecycle, updater and packaging | Keep; add the Python Sidecar manager as a product-specific module |
| `src-tauri/plugins/tauri-plugin-llamacpp` | Local model process | Keep without a parallel reimplementation |
| `src-tauri/plugins/tauri-plugin-hardware` | Model compatibility and hardware status | Keep |

Jan's `rag-extension`, vector database plugin, general chat domain, assistant
domain, Jan branding, screenshots, and trademark assets are not product
foundations. The AGPL RAG implementation is not distributed in the closed
product; Python implements the V1 retrieval gateway after scope authorization.

## Product-owned additions

The following remain original product code and are integrated into the Jan
baseline:

- `apps/story-engine`: FastAPI, Markdown workspace, evolution, review,
  ModelGateway, Writer and retrieval authorization;
- `packages/contracts`: generated Story Engine API contracts;
- story submission, workbench, evolution, character, world, event and
  manuscript routes;
- Tauri management of the Python Sidecar only.

## Credential boundary

Jan Provider screens remain the user-facing configuration surface. Provider
secrets must be bridged to the Python gateway through an operating-system
secret store or a token-scoped runtime handoff. They must never be copied into
story projects, query strings, browser storage, status events, or logs.

## Attribution

Every migrated or modified Jan directory retains its upstream license. The
repository notice records the locked commit, moved paths, substantive product
changes, and removal of Jan marks. New Story Engine files are not labeled as
Jan-derived.
