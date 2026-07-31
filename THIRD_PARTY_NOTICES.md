# Third-Party Notices

This repository currently records upstream baselines but does not yet distribute
their source code or binaries.

## Jan

- Project: Jan
- Source: https://github.com/janhq/jan
- Locked version: `v0.8.4`
- Commit: `5f30aee467f08941964a83f946e2663e7ae0e01f`
- License: Apache License 2.0
- Copyright: 2025 Menlo Research
- Modification status: no Jan source files imported at this baseline
- Trademark note: Jan names, logos, illustrations, and branded assets are not
  used by AI Story Evolution Engine Next.

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

These are consumed as package dependencies; their source files and trademarks
have not been copied or modified. Exact resolved versions are recorded in
`pnpm-lock.yaml` and `apps/desktop/src-tauri/Cargo.lock`.
