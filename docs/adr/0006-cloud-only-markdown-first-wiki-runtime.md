# ADR-0006: Cloud-only, Markdown-first LLM Wiki runtime

- Status: Superseded for product scope and semantic authority by ADR-0009
- Date: 2026-08-03
- Supersedes: ADR-0002 EventCommitService clauses, ADR-0004 local-model
  clauses, and ADR-0005 JSON authority and RAG clauses
- Relates to: ADR-0001, ADR-0003

> Historical implementation decision. ADR-0009 makes validated ResolvedEvents,
> Checkpoints, Actor State, and Memory the semantic authority and makes Wiki
> and Manuscript projections. The cloud-provider and atomic-write evidence in
> this record may remain useful, but Markdown-first Wiki maintenance cannot be
> a World Session truth or recovery dependency.

## Context

The product now has one closed story path: Jan cloud Providers feed a persistent
Concordia simulation, the Game Master resolves world facts, branch-scoped World
and Character Wikis maintain long-term knowledge, and Narrative Sources feed
Writer and Editor. The remaining JSON/JSONL runtime authority, path-order Wiki
truncation, partial step commits, and swallowed Wiki failures created parallel
truth and recovery rules.

The project has not shipped, so compatibility stores and dual formats would add
cost without preserving user data.

## Decision

Jan's existing remote Provider configuration and keychain remain the sole model
infrastructure. Story Engine stores only task profiles and model references. It
does not bundle or start llama.cpp, MLX, GGUF models, embedding models, vector
databases, rerankers, or a second Provider registry.

Markdown is the only authoritative story persistence format. Structured state
uses a small YAML front matter envelope plus an exact fenced JSON payload, so
Concordia checkpoints remain lossless while the file itself stays inspectable
and exportable. Authority is organized as:

```text
.story-engine/runtime/sessions/{session}.md
.story-engine/runtime/branches/{branch}.md
.story-engine/runtime/checkpoints/{checkpoint}.md
history/turns/{branch}/{step}-{trace}.md
history/observations/{branch}/{actor}/{source}.md
.story-engine/manuscript/{branch}/drafts/{scene}.md
wiki/branches/{branch}/director-instructions/{instruction}.md
.story-engine/runtime/output-failures/{failure}.md
.story-engine/config/model-policy.md
```

Project indexes remain disposable caches. Transaction recovery manifests are
ephemeral write-ahead metadata, never story authority, and are deleted after
commit or rollback.

`SimulationCommitKernel` owns one recoverable Step transaction. It prepares and
commits Raw Turn, Raw Observations, Session, optional Checkpoint, and Branch Head
through one `AtomicBatch`. Branch compare-and-swap is checked while holding the
project lock; the Branch document is the final replacement in the same batch.
A failed file replacement restores every prior file and leaves no orphan turn.

The Wiki Source Reader consumes immutable Raw Markdown. `WikiContextBuilder`
uses deterministic routing rather than embeddings or path-order truncation:
mandatory self/profile/goals/plans/current state, current relationships,
locations and entities, explicit index links, keyword matches, then recent or
high-confidence pages. Every result carries a manifest with path, reason,
permission, source IDs, and estimated tokens.

Wiki maintenance is blocking state. If consolidation fails at a scene or
chapter boundary, the Session becomes paused with `maintenance_status=failed`,
the error and source boundary are persisted, and step/run/resume are rejected.
An explicit retry must succeed before simulation can continue. Manuscript
generation remains derived output and may fail without rolling back history.

## Consequences

### Positive

- One human-inspectable persistence format carries both narrative and exact
  runtime state.
- Every Wiki source can resolve to immutable Raw Markdown.
- Step restoration needs no orphan-record heuristics.
- Context selection is deterministic, privacy-labelled, and explainable.
- A new scene cannot run against a known-stale Wiki.
- Packaged applications depend only on user-configured cloud Providers.

### Negative

- Markdown envelopes are larger than compact JSONL.
- Append-only turns use one file per attempt, increasing file counts.
- Context routing requires explicit scene hints and long-form stress tests.

### Neutral

- Fenced JSON remains JSON syntax, but it is content inside the canonical
  Markdown document, not a parallel persistence format.
- Concordia's lightweight in-process hash scorer is not a model, download,
  service, persistent index, or P0 packaging burden. It is outside this
  decision; deterministic Wiki routing never uses it.

## Superseded statements

- ADR-0002 no longer retains `EventCommitService` as a mutation boundary.
- ADR-0004 no longer retains Jan model downloads, llama.cpp, or MLX sessions;
  only the remote Provider proxy and keychain boundary remain.
- ADR-0005 no longer treats JSON checkpoints/logs as authority, no longer
  accepts log-before-head partial commits, and no longer retains RAG as an
  optional story context path.
- Markdown is not merely a disposable simulation projection. Raw history,
  checkpoints, manifests, Wiki, and drafts are authoritative Markdown.

## Acceptance

This decision is enforced by tests for the OutputPolicy contract, Markdown-only
story paths, Nth-file transaction failure and recovery, Raw Source resolution,
hundreds-page context relevance, Wiki failure pause/block/retry, Sidecar restart
consistency, and forbidden architecture symbols.
