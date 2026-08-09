# ADR-0009: Living Story World Product Boundary

- Status: Accepted
- Date: 2026-08-09
- Authority: [Living Story World PRD v1.0](../product-plan.md)
- Supersedes: product-scope and authority clauses in ADR-0006 and ADR-0007
- Amends: ADR-0003, ADR-0004, ADR-0005, and ADR-0008

## Context

The product is no longer an AI novel-authoring system whose primary workflow
is submission, multi-Agent story evolution, editorial review, and chapter
generation. It is a Persistent AI World Simulator. Existing implementation and
records still contain the earlier definition, so technical decisions need one
explicit precedence boundary.

## Decision

The product protocol is:

    World -> Perception -> Intent -> Resolution -> ResolvedEvent

Player and important NPCs use the same Actor rules. Actor text expresses an
attempt; only Concordia Game Master Resolution may produce a validated
ResolvedEvent and commit world effects.

The authoritative runtime data is validated ResolvedEvents, Checkpoints,
Branch lineage, Actor State, and Memory. World Truth, Actor Knowledge, Actor
Belief, and Perception are distinct.

Wiki, Narrative, UI Scene, Summary, and Manuscript are projections. They may
be useful and human-readable, but they are never prerequisites for committing,
restoring, or branching a World Session. No maintenance failure in a
projection may redefine committed world history.

World Session is the MVP product loop. Writer, Editor, submission chat,
chapter/manuscript generation, RAG authoring workspaces, and automatic
npc-to-active editorial promotion are retained legacy or future capabilities,
not core dependencies.

Concordia remains the only simulation kernel. Jan remains the desktop/model
foundation. Do not create a parallel Player, World, Resolution, Combat,
Inventory, or Time engine.

## Format boundary

This ADR decides semantic authority, not whether a store serializes state as
JSON, JSONL, Markdown, or another validated format. A serialization ADR may
change physical files without changing the product protocol. No file format,
Wiki page, or model-generated prose becomes a second semantic authority.

## Consequences

- Older authoring plans remain historical evidence but cannot define current
  scope.
- The World Session may reuse existing runtime, persistence, model-gateway,
  and branch code without depending on old authoring workflows.
- Existing Writer, Editor, Wiki, RAG, and manuscript code can remain while the
  product is unshipped, but it must be removable from the MVP path.
- Architecture and acceptance work should be derived from the 12 PRD cases and
  the World Simulation MVP completion definition.
