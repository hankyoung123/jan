# Story Evolution Context

This context defines the people and agency concepts used by the story simulation.

## Character Agency

**NPC**:
A registered story person who does not independently choose simulation actions.
_Avoid_: Agent NPC, dynamic entity

**Active Agent**:
A registered character who independently chooses actions toward a continuing goal.
_Avoid_: Active entity, roster member

**Active Agent Pool**:
All Active Agents available on one story branch, independent of current scene participation.
_Avoid_: Scene roster, active roster

**Scene Roster**:
The one to four Active Agents selected to participate in the current scene.
_Avoid_: Active Agent Pool, all active characters

**Acting Agent**:
The single Scene Roster member selected to act during one simulation step.
_Avoid_: Participant, scene roster

## Manuscript Lineage

**Target Checkpoint**:
The immutable story-state version selected as the upper boundary of one manuscript source.
_Avoid_: Current head, latest state

**Reachable History**:
The committed turns on the Target Checkpoint's parent chain, excluding abandoned turns that merely share its branch or step numbers.
_Avoid_: Branch log, step range

**Writer Context**:
The privacy-limited manuscript context containing reachable events, the Target Checkpoint's Wiki, and only the chosen viewpoint's authorized memories.
_Avoid_: GM context, omniscient context

**Editor Context**:
The complete manuscript verification context for the same Target Checkpoint and source range.
_Avoid_: Writer Context, generation context
