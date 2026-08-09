# Living Story World Engineering Documentation

This directory contains the current product and engineering documentation for
Living Story World (code name: AI Story Evolution Engine).

## Authority order

1. [product-plan.md](product-plan.md) — the highest-level product definition,
   principles, MVP scope, interaction model, and architecture boundaries.
2. Accepted, non-superseded records under [adr/](adr/) — implementation
   decisions within the PRD boundary.
3. [architecture.md](architecture.md), [domain-model.md](domain-model.md),
   [data-contracts.md](data-contracts.md), and [ui-flow.md](ui-flow.md) —
   current engineering interpretation and repository contracts.
4. Dated files under [plans/](plans/) — implementation plans and audit
   evidence, not independent product authority.

If a lower-level document conflicts with the PRD, the PRD wins. Legacy Writer,
Editor, submission, manuscript, RAG, and story-authoring documents describe
retained code or historical work unless the PRD explicitly brings them into
scope.

See [ai-coding-guide.md](ai-coding-guide.md) before changing implementation.
The locked Jan baseline and migration boundary are recorded under
[upstream/](upstream/).
