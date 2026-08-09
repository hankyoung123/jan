# Living Story World Domain Model

The product vocabulary is exactly **World, Actor, Perception, Intent,
Resolution, and Memory**. Existing Python class names are implementation
details and map into this protocol; they do not define a second product model.

## Product concepts

- **World**: objective time, locations, rules, environment, hidden truth,
  important resources, pressures, and committed history.
- **Actor**: shared abstraction for the player and important NPCs, including
  identity, knowledge, capabilities/experience, conditions, resources,
  relationships, location, goal, beliefs, and private Memory.
- **Perception**: read-only, Actor-scoped projection of what can currently be
  perceived or known.
- **Intent**: natural-language attempt submitted by an Actor.
- **Resolution**: Game Master adjudication of Intent against current Reality.
- **Memory**: what an Actor experienced and remembers, separate from its
  current state.

The model does not require generic HP, strength, skill-level, success-chance,
action-permission, combat, inventory-engine, or quest fields. A particular
world may define explicit numbers only when its own rules require them.

## Current implementation mapping

- WorldState and checkpointed component state implement current World state.
- Character is the current Actor projection. Player and NPC actions use the
  same putative action and Resolution semantics.
- ActorStateContext is a deterministic Concordia-facing projection of
  Character, refreshed after state effects and checkpoint restore; it owns no
  independent state.
- TurnSessionRequest.player_actor_id identifies the human-controlled Actor.
- ActionSpec is the serializable Concordia request boundary.
- ResolutionStateUpdate is the narrow, validated effect boundary.
- ResolvedEvent is the only event type that may enter committed history.
- MemoryRecord and MemorySnapshot preserve ownership, visibility, sources,
  step, locale, and integrity metadata.
- TurnSessionSnapshot is the complete recoverable runtime state.
- PlayerPerception is a restricted projection, not a World snapshot.
- BranchManifest and immutable checkpoints define history lineage.
- ModelCallTrace and TurnTrace provide provenance, never world truth.

Legacy Project, Fact, StoryEvent, SceneDraft, Wiki, and manuscript models may
remain as seed inputs, authoring data, or projections. They cannot directly
advance a World Session branch head.

## Invariants

1. User and NPC text are Intent, never committed outcomes.
2. Only a validated ResolvedEvent can change World or Actor state.
3. World Truth, Actor Knowledge, and Actor Belief are separate.
4. An Actor cannot retrieve another Actor's private Memory.
5. Perception contains only authorized visible events and the requesting
   Actor's own relevant state.
6. Resolution cannot create an unestablished key resource, capability, or
   decisive piece of evidence from an assertion.
7. Actor capability, condition, resources, environment, other Actors, and time
   constrain outcomes.
8. World time is persistent and monotonic within a branch.
9. A branch head advances only after its checkpoint and committed event record
   are durable and validated.
10. A Branch created from a Checkpoint has independent future history and
    cannot move the source branch head.
11. Wiki, Narrative, UI Scene, Summary, and Manuscript are rebuildable
    projections and are not required for restoration.
12. IDs, enums, references, paths, and hashes are locale-independent.
13. Important NPCs and the player obey the same Resolution rule; runtime
    scheduling may still avoid unnecessary NPC model calls.
