# Third-Party Notices

This repository is a product fork of the locked Jan baseline and adds an
original Python Story Engine and story-focused React product layer.

## Jan

- Project: Jan
- Source: https://github.com/janhq/jan
- Locked version: `v0.8.4`
- Commit: `5f30aee467f08941964a83f946e2663e7ae0e01f`
- Root repository license: Apache License 2.0
- Copyright: 2025 Menlo Research
- Included source: `web-app`, `core`, selected assistant/conversation extensions,
  `src-tauri`, build scripts, and supporting tests from the locked commit
- Excluded source: Jan's `rag-extension`, `vector-db-extension`,
  `tauri-plugin-rag`, `tauri-plugin-vector-db`, marketing website, end-user
  documentation site, changelog media, and promotional repository assets
- Modification status: desktop package metadata, icons, routes and domain
  workflows are rebranded for Story Engine; the inherited marketing/docs,
  RAG, model download, hardware inspection, and local inference infrastructure
  are removed
- Trademark note: Jan names, logos, illustrations, and branded assets are not
  part of the product identity and must be removed from distributed builds.

## Concordia

- Project: Concordia
- Source: https://github.com/google-deepmind/concordia
- Locked version: `v2.4.0`
- Commit: `702998f57da71f87bf4e607abc1325ee51cca21f`
- Package: `gdm-concordia==2.4.0`
- License: Apache License 2.0
- Copyright: 2023 DeepMind Technologies Limited
- Included code: unmodified `gdm-concordia==2.4.0` Python dependency
- Integration boundary: Story-owned code under
  `apps/story-engine/src/story_engine/concordia_adapter` converts domain
  contracts to Concordia entities and routes every language-model call through
  the existing Jan-backed `ModelGateway`
- License text: `licenses/Concordia-2.4.0-APACHE-2.0.txt`

Full license text and per-directory migration records belong in `licenses/`.

## Novel

- Project: Novel
- Source: https://github.com/steven-tey/novel
- Package: `novel@1.0.2`
- License: Apache License 2.0
- Copyright: Steven Tey and Novel contributors
- Included code: the published React editor package and its Tiptap 2 dependency
  line; no Novel branding, hosted service, AI completion endpoint, or trademark
  assets are included
- Integration boundary: Story-owned code under `web-app/src/editor` adapts
  Novel to the Jan desktop shell and shadcn/ui controls; model generation and
  canonical Markdown writes remain in the Python Story Engine
- License text: the standard Apache License 2.0 text is included at
  `licenses/Concordia-2.4.0-APACHE-2.0.txt`

Novel's Tiptap 2 editor dependencies are MIT licensed. Their exact resolved
versions and transitive dependency graph are recorded in `yarn.lock`.

## Desktop runtime and UI dependencies

The desktop product currently links or bundles the following package families:

- Tauri 2.x and its Rust dependencies: Apache-2.0 / MIT
- React 19 and React DOM: MIT
- React Router: MIT
- Lucide React: ISC
- Source Sans 3 variable font: SIL Open Font License 1.1
- Newsreader variable font: SIL Open Font License 1.1

Jan's direct and transitive JavaScript versions are recorded in `yarn.lock` and
its Rust versions in `src-tauri/Cargo.lock`.

The retained `assistant-extension` package manifest declares `AGPL-3.0`,
despite the Jan repository root carrying Apache-2.0. That
package-level conflict must be resolved through upstream clarification,
license-compliant distribution, or replacement before a closed-source release.
The exact license text is included at `licenses/AGPL-3.0.txt`.

The affected retained package paths are:

- `core/package.json`
- `extensions/assistant-extension/package.json`

Including the text is an attribution and notice requirement, not a license
decision. The product must not relabel these packages as MIT or Apache-2.0.

## Python runtime dependencies

The Story Engine consumes Concordia, FastAPI, Pydantic, Uvicorn, HTTPX,
jsonschema, and python-frontmatter under their respective permissive licenses.
Exact resolved versions and transitive dependencies are recorded in
`apps/story-engine/uv.lock`. Provider credentials remain in Jan's operating-
system secret storage and are not persisted in project files.
