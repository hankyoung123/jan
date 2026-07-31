# UI Flow

The desktop navigation contains Workbench, Evolve, Characters, World, Events,
Manuscript, Model Center, and Project Settings. Global settings remain separate
from project settings.

The primary evolution flow is linear and visible:

```text
Current situation -> Character intents -> World resolution
-> Editorial review -> User approval -> Manuscript
```

The user can confirm, request revision, or discard. Any candidate edit visibly
invalidates its prior review. Internal implementation terms such as Concordia
components are not exposed in product copy.

Workbench is a compact operational view: current world state, pending work,
latest event, active characters, and one primary "Advance next turn" action.
The right inspector is collapsible. Green means confirmed, amber means pending
or risky, and red means blocked or failed.

