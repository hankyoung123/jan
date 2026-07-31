# AI Coding Guide

Before changing a module, read:

- `docs/product-plan.md`
- `docs/architecture.md`
- `docs/domain-model.md`
- `docs/data-contracts.md`
- relevant ADRs and tests

Each change must have one measurable objective, explicit allowed files, an
input/output contract, acceptance criteria, and verification commands.

Use contract-first TDD:

```text
contract -> failing test -> minimal implementation -> test -> refactor -> docs
```

Do not let model output write Markdown, duplicate canonical domain state in
React, treat retrieval as authorization, introduce a second source of truth, or
change a core technology without an ADR.

