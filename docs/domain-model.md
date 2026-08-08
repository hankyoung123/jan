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
- `TurnSessionSnapshot.characters` is the branch-local projection for every
  registered person. `roster_actor_ids` contains the one to four Active Agents
  selected for the current scene, not the complete Active Agent Pool.
- `StepResult` records the actor action and resolved world result for one step.
- `BranchManifest` points to an immutable head checkpoint and records fork
  ancestry.
- `ModelCallTrace` and `TurnTrace` provide request-to-world-event provenance.

For World Simulation MVP, the product vocabulary is `World`, `Actor`,
`Perception`, `Intent`, `Resolution`, and `Memory`:

- `WorldState` is current location/time/rules/pressures and public scene text;
  its causal changes are represented by committed events and owned state
  effects.
- `Character` is the shared Actor abstraction for human and NPC. It includes
  identity, knowledge references, capabilities, conditions, resources,
  relationships, location, and beliefs. There are no generic strength, skill,
  HP, or action-permission fields.
- `TurnSessionRequest.player_actor_id` identifies a human Actor. The durable
  snapshot records the same `player_actor_id` and current `WorldState`.
- `ResolutionStateUpdate` is the minimal structured GM boundary. Local code
  binds display names to IDs and rejects unsupported paths or unowned resources
  before a checkpoint can advance.
- `PlayerPerception` is derived from the checkpoint and event visibility. It is
  not a copy of World Truth.

The legacy project seed models (`Project`, `WorldState`, `Character`, `Fact`)
remain inputs for constructing initial actor and Game Master memories. They are
not rewritten after every simulation step. Writer scenes and optional editorial
amendments remain separate derived authoring workflows.

Core invariants:

1. A character cannot retrieve another character's private memory bank.
2. Actor actions are putative until resolved by the Game Master.
3. Checkpoint IDs derive from canonical state hashes.
4. A branch head advances only after its checkpoint and raw log are durable.
5. The Wiki is rebuildable from durable history and never required for
   restoration.
6. `content_locale` affects prose, never IDs, enums, tags, or hashes.
7. A Game Master may create an `npc`, but only the Editor may promote it to an
   `active` Actor, at a completed scene boundary and with event evidence.
8. Structured participant IDs resolve to registered Characters; private
   observation owners resolve to active Actors.
9. The Active Agent Pool has no fixed size limit; a Scene Roster contains at
   most four Active Agents, and one Acting Agent acts per simulation step.
10. User and NPC text are putative intents. Only a valid GM `ResolvedEvent`
    can commit a world consequence.
11. World Truth, Actor Knowledge, and Actor Belief are separate. A belief is
    never promoted to a world fact by assertion alone.
12. Player perception contains only physical/public/participant-visible event
    information and the player's own state.
