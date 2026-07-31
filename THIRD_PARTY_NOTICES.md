# Third-Party Notices

This repository is a product fork of the locked Jan baseline and adds an
original Python Story Engine and story-focused React product layer.

## Jan

- Project: Jan
- Source: https://github.com/janhq/jan
- Locked version: `v0.8.4`
- Commit: `5f30aee467f08941964a83f946e2663e7ae0e01f`
- License: Apache License 2.0
- Copyright: 2025 Menlo Research
- Included source: `web-app`, `core`, `extensions`, `src-tauri`, build scripts,
  and supporting tests from the locked commit
- Modification status: desktop package metadata, icons, routes and domain
  workflows are rebranded for Story Engine; the model, Provider and local
  inference infrastructure is retained
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
- Modification status: dependency not yet installed; future use is through an
  unmodified package behind `concordia_adapter`

Full license text and per-directory migration records belong in `licenses/`.

## Desktop runtime and UI dependencies

The desktop product currently links or bundles the following package families:

- Tauri 2.x and its Rust dependencies: Apache-2.0 / MIT
- React 19 and React DOM: MIT
- React Router: MIT
- Lucide React: ISC
- Source Sans 3 variable font: SIL Open Font License 1.1
- Newsreader variable font: SIL Open Font License 1.1

Jan's direct and transitive JavaScript versions are recorded in `yarn.lock` and
its Rust versions in `src-tauri/Cargo.lock`. Temporary pre-integration desktop
dependencies remain recorded in `pnpm-lock.yaml` until that duplicate tree is
removed.

## Python runtime dependencies

The Story Engine consumes FastAPI, Pydantic, Uvicorn, HTTPX, jsonschema,
python-frontmatter, and keyring under their respective permissive licenses.
Exact resolved versions and transitive dependencies are recorded in
`apps/story-engine/uv.lock`. Keyring is used only as the operating-system secret
storage adapter; Provider credentials are not persisted in project files.
