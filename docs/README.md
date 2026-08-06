# Story Engine Engineering Documentation

This directory contains the authoritative product and engineering documents for
AI Story Evolution Engine Next.

## Start here

- [`product-plan.md`](product-plan.md) — product scope, implementation phases,
  quality gates, and V1 acceptance criteria.
- [`architecture.md`](architecture.md) — React, Tauri, and Python ownership
  boundaries.
- [`domain-model.md`](domain-model.md) — canonical aggregates and invariants.
- [`data-contracts.md`](data-contracts.md) — HTTP, WebSocket, and generated
  contract rules.
- [`ui-flow.md`](ui-flow.md) — user-facing information architecture.
- [`ai-coding-guide.md`](ai-coding-guide.md) — required change workflow.

Architecture decisions live under [`adr/`](adr/). Implementation plans live
under [`plans/`](plans/). The locked Jan baseline and migration boundary are
recorded under [`upstream/`](upstream/).

The inherited Jan marketing website, end-user documentation, changelog, and
promotional assets are intentionally excluded. Refer to the locked upstream
repository when historical Jan documentation is needed; do not copy it back
into the product documentation tree.
