# Runtime domain model

The runtime contracts are strict, immutable Pydantic models under
`story_engine/domain`. Machine identifiers use stable lowercase IDs and are not
translated.

- `ActionSpec` is the serializable boundary for Concordia action requests.
- `MemoryRecord` and `MemorySnapshot` preserve ownership, visibility, source
  records, step, locale, and integrity hashes.
- `ResolvedEvent` and `ResolvedTurn` are lightweight projections of Game
  Master decisions; natural-language resolution remains authoritative.
- `TurnSessionRequest` combines project, branch, actors, locale, premise, and
  `ControlPolicy`.
- `TurnSessionSnapshot` is the complete recoverable session state.
- `StepResult` records the actor action and resolved world result for one step.
- `BranchManifest` points to an immutable head checkpoint and records fork
  ancestry.
- `ModelCallTrace` and `TurnTrace` provide request-to-world-event provenance.

The legacy project seed models (`Project`, `WorldState`, `Character`, `Fact`)
remain inputs for constructing initial actor and Game Master memories. They are
not rewritten after every simulation step. Writer scenes and optional editorial
amendments remain separate derived authoring workflows.

Core invariants:

1. A character cannot retrieve another character's private memory bank.
2. Actor actions are putative until resolved by the Game Master.
3. Checkpoint IDs derive from canonical state hashes.
4. A branch head advances only after its checkpoint and raw log are durable.
5. Markdown projections are rebuildable and never required for restoration.
6. `content_locale` affects prose, never IDs, enums, tags, or hashes.
