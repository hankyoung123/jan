# AI Coding Guide

Before changing a product or simulation module, read in order:

1. docs/product-plan.md
2. docs/adr/0009-living-story-world-product-boundary.md
3. docs/architecture.md
4. docs/domain-model.md
5. docs/data-contracts.md
6. relevant non-superseded ADRs and tests

The PRD is the highest-level authority. A dated implementation plan, audit,
legacy UI, or existing code path does not override it.

Each change must have one measurable objective, explicit allowed files, an
input/output contract, acceptance criteria, and verification commands. Use:

    contract -> failing test -> minimal implementation -> test -> refactor -> docs

For World Session work, preserve these gates:

- User and NPC output are putative Intent.
- Only validated ResolvedEvent commits consequences.
- World Truth, Knowledge, Belief, Memory, and Perception remain separated.
- Actor capability, condition, resources, environment, other Actors, and time
  constrain Resolution.
- Critical evidence and resources cannot be generated from assertion.
- Checkpoint/Branch lineage is the only rewind path.
- Player-facing APIs never expose hidden or private state.
- Concordia remains the only simulation kernel.

Do not add a PlayerEngine, WorldEngine, ResolutionEngine, CombatEngine,
InventoryEngine, action-permission list, second source of truth, browser-owned
runtime state, or projection-required recovery path.

Writer, Editor, submission, manuscript, RAG, and old multi-Agent authoring code
may be maintained only as explicitly scoped legacy or future projection work.
Do not make them a World Session dependency unless the PRD is revised first.
