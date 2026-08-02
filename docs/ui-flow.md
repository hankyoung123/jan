# UI Flow

The desktop navigation contains Workbench, Evolve, Characters, World, Events,
Manuscript, Model Center, and Project Settings. Global settings remain separate
from project settings.

## Simulation console

Evolve is a control surface for one branch-local simulation session:

```text
Choose branch and actors -> Start session -> Step or run
-> Pause/checkpoint -> Resume, fork, roll back, project, or terminate
```

Starting a session sends the selected actor IDs, premise, content locale, and
control policy to the Python Story Engine. The page then renders the server
snapshot and never maintains a second browser-owned copy of actor, Game Master,
memory, step, or branch state.

Step executes exactly one Concordia step and returns to a paused boundary. Run
and resume use the same engine with scene, chapter, or autonomous control
policies. Pause takes effect at a safe step boundary. Terminate ends the
session; checkpoint persists a restorable branch head. Events do not require
per-event approval.

The console can fork from a checkpoint and rebuild disposable Markdown views
for a branch. Rollback remains a server operation and changes only the selected
branch head. Deleting `world.md`, `timeline.md`, or `characters.md` does not
delete simulation state.

The content-locale control is available only at created or paused boundaries.
It changes subsequent actor, Game Master, observation, event, and Writer prose;
historical content and machine identifiers are not translated.

When no project is selected, the page links back to Submission instead of
assuming a bundled example. The UI shows stable Story Engine errors and does
not expose Concordia component internals, prompts, credentials, or provider
SDK details.

## Other workspaces

Characters, World, and Events load server-owned project or projection data.
NPC promotion remains an explicit editorial workflow separate from simulation
world-event validity. Manuscript changes and amendments retain their own review
and commit semantics; they do not gate the Game Master's world resolution.

Submission starts from a non-canonical setting draft. Its discussion panel
sends history and the draft to the Python Editor profile, shows the returned
creative direction and runnable requirements, and writes project seed files
only when finalization succeeds.

The discussion panel reuses Jan's conversation and composer components. Jan
thread persistence and direct provider inference are not mounted in this flow.
Tauri owns desktop lifecycle and the model bridge; the Python sidecar owns
story-domain state and simulation.

## Model Center and Workbench

Model Center keeps Jan's catalog, download, Provider, and local-runtime
workflows. The task-model band configures Actor, Game Master, Reflection,
Memory Consolidation, Projection, Editor, Writer, and Embedding profiles. It
contains no credential fields and links to Jan Provider settings.

Workbench is a compact operational overview of the active project, current
branch/session state, recent events, and active characters. HTTP snapshots are
authoritative; WebSocket events provide ordered live updates and resync hints.
