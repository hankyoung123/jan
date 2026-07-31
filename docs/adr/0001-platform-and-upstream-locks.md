# ADR-0001: Platform and upstream locks

- Status: Accepted
- Date: 2026-07-31

## Decision

Use React with Tauri for the desktop product and Python 3.12 with FastAPI for
the story engine. Register Jan as Git remote `upstream` and lock the reference
baseline to Jan `v0.8.4`
(`5f30aee467f08941964a83f946e2663e7ae0e01f`). Lock Concordia to `v2.4.0`
(`702998f57da71f87bf4e607abc1325ee51cca21f`) when the adapter is introduced.

Use pnpm for the new monorepo even though the locked Jan baseline uses Yarn.
Migrated Jan files must be adapted into the new workspace rather than importing
Jan's package-manager assumptions wholesale.

## Rationale

Jan supplies a proven Tauri/local-model reference and Apache-2.0 reusable code.
The product plan requires a Python domain boundary and the current Concordia
release requires Python 3.12 or newer. A clean workspace makes licensing and
domain ownership explicit while preserving a precise upstream reference.

## Consequences

Every migrated upstream directory requires attribution and a modification note.
Jan trademarks and brand assets are excluded. Concordia is accessed only through
an adapter and is not forked in V1.

