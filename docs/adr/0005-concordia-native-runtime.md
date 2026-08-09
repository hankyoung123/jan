# ADR-0005: Adopt a persistent Concordia-native simulation runtime

- Status: Accepted for the Concordia runtime core; amended by ADR-0009
- Date: 2026-08-02
- Supersedes: the per-step Markdown authority and mandatory approval portions
  of ADR-0002
- Relates to: ADR-0001, ADR-0003, ADR-0004

ADR-0006 supersedes this record's JSON authority, partial commit, optional RAG,
and local-model statements. The persistent Concordia and Game Master decisions
remain accepted.

ADR-0009 now supplies the product authority boundary: validated ResolvedEvents,
Checkpoints, Actor State, and Memory are authoritative, while Wiki, Narrative,
Summary, UI Scene, and Manuscript are projections. Physical serialization is a
store-level decision.

## Context

The current `ConcordiaStoryAdapter` creates a temporary character entity for
one intent and a temporary resolver entity for one outcome. `EvolutionService`
owns actor scheduling, builds a batch of intents from one snapshot, asks a
separate resolver for a fixed `WorldOutcome`, and stores a `TurnCandidate`
until the user approves it. This pays the integration cost of Concordia without
using persistent entities, private associative memories, a shared Game Master
memory, dynamic action specifications, termination, the sequential engine, or
checkpoint restoration.

Batch resolution also prevents immediate reactions and makes new kinds of
world change depend on expanding a fixed schema. Mandatory approval of every
event prevents scene-, chapter-, and autonomous-control modes from sharing one
runtime lifecycle.

## Decision

Use unmodified `gdm-concordia==2.4.0` as the sole Entity/Component/Engine
implementation. Story Engine will add only world-specific recipes, components,
domain contracts, persistence, and application services around it.

Each active simulation branch has:

- persistent character entities with independent associative memory banks;
- one shared Game Master memory bank and persistent Game Master components;
- dynamic `NEXT_ACTING`, `NEXT_ACTION_SPEC`, `RESOLVE`, and `TERMINATE` calls;
- a sequential step lifecycle in which an actor action is putative until the
  Game Master resolves it;
- append-only raw logs and content-addressed or sequenced checkpoints;
- step, scene, chapter, and autonomous control policies with hard resource
  limits;
- optional event/effect projections for UI, retrieval, and integration.

`ModelGateway` remains the only model-access boundary. Concordia components do
not call Provider SDKs, read credentials, or write canonical state. Jan remains
the Provider, model, download, and local-runtime authority described by
ADR-0004.

## Canonical state

For a running branch, recoverable truth is the branch manifest plus its head
checkpoint, Game Master shared memory, character private memories, entity and
component state, and append-only raw log. Markdown is a project seed, an
editable human input, or a rebuildable projection/export; it is not required
to be rewritten after every engine step.

The commit protocol writes logs and checkpoint data before atomically advancing
the branch head. A cancelled or failed model call cannot advance the head or
leave a half checkpoint. Projections may be deleted and reconstructed without
affecting restoration.

## Privacy and language boundaries

The Game Master owns world truth and resolved events. A character can retrieve
only its own memory bank plus observations explicitly routed to it. Private
reasoning is not copied to other characters or implicitly exposed to the Game
Master.

`content_locale` controls natural-language instructions, observations, actions,
events, reasons, and summaries. IDs, enums, tags, references, paths, and hashes
remain stable machine fields and are never translated. `ui_locale` remains a
front-end concern.

## Removed architecture

After the new runtime and persistence path meet their acceptance tests, remove
`ConcordiaStoryAdapter`, `TurnGenerator`, batch intent scheduling,
`WorldOutcome`, `FactCandidate`, mandatory `KnowledgeChange`, `TurnCandidate`,
`CandidateStore`, and the rule that every event requires user approval. Do not
retain aliases, old-data migrators, dual schemas, or a parallel production
path; the product has not shipped.

## Consequences

### Positive

- Conversations, pursuit, negotiation, and reactions can evolve one step at a
  time against persistent memories.
- Scheduling, action form, resolution, and semantic termination have one
  persistent world authority.
- Pause, resume, replay, branching, and rollback use explicit checkpoints.
- Unknown event types remain natural-language facts instead of forcing schema
  expansion.
- Story Engine does not duplicate Concordia lifecycle primitives.

### Negative

- Existing turn, candidate, fact, review, API, and projection flows require a
  coordinated replacement.
- Memory drift, context growth, checkpoint size, and long-running model budgets
  require dedicated controls and soak tests.
- The UI must move from per-event approval to session and branch controls.

### Neutral

- Atomic writes, project locks, transaction recovery, and WebSocket events
  remain runtime infrastructure. Retained Writer, Editor, RAG, and Markdown
  export paths are optional projections and legacy/future capabilities under
  ADR-0009.

## Alternatives considered

### Keep the shallow adapter

Rejected because it preserves the batch state machine and receives none of the
core lifecycle, memory, scheduling, or checkpoint benefits.

### Build a separate Story Kernel

Rejected for the initial release because it duplicates tested entity,
component, engine, observation, and recovery concepts before project-specific
needs prove that a fork is required.

### Fork Concordia

Rejected because upstream merge and security maintenance costs are not yet
justified. Project-specific behavior can be implemented with prefabs,
components, codecs, and persistence adapters.

## Rollback

The session API is now the only production evolution path. Rollback means
reverting the migration as a whole, not maintaining a permanent dual runtime.

## Acceptance

The migration is complete when the repository satisfies the executable checks
in the 2026-08-02 deep-research report: persistent and isolated actor memories,
shared Game Master memory, validated dynamic actor/action selection, putative
versus resolved event separation, checkpoint hash equivalence, session and
branch controls, projection rebuilds, traceability, cancellation atomicity,
100-step deterministic replay, strict lint/type/test gates, and no forbidden
legacy symbols or compatibility aliases.
