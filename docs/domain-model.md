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
- **World Initiative**: the same Game Master producing an external world change
  after a deterministic World trigger, without authoring Actor cognition.
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
- Project Fact records are the immutable canonical seed. ResolverContext selects
  only relevant Facts and combines them with current World/Actor projections,
  recent committed events, and the current Intent.
- InitiativeContext contains pressures, clocks, relevant World state, and a
  small recent causal window. It contains no Actor belief or goal authority.
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
14. Wiki is non-authoritative and excluded from ResolverContext; it cannot
    override Canonical Truth or become necessary for Resolution.
15. World Session scene boundaries do not automatically promote ordinary NPCs,
    create Actor memory, or edit Wiki pages.
16. `core_desire` is persistent and required; `current_goal` is transient and
    may be absent for any active Actor at runtime.
17. Only an Actor owns its beliefs, current goal, intent, voluntary speech, and
    voluntary action. GM effects may change physical Character state and World
    state, but never those cognition fields.
18. Resolution and World Initiative are modes of one Game Master and both emit
    the same ResolvedEvent history type.
