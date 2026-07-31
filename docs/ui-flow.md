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

The desktop UI persists only the selected Story Engine project identifier. On
entry to Evolve it loads the canonical project snapshot from the Python
sidecar, derives the active participants and display names from that snapshot,
and renders the current world version, time, location, incident, and pressures.
It does not keep a second browser-owned copy of world or character state.

After a turn candidate exists, generation is locked until the user chooses one
of the three V1 decisions: request revision, discard, or confirm. Confirmation
writes through the Story Engine and then reloads the canonical project snapshot
before the UI displays the new world version. When no project is selected, the
page links back to Submission instead of assuming a bundled example project.

Submission begins with an empty, non-canonical setting draft. The discussion
panel sends user/assistant history and that draft to the Python Editor profile;
the adjacent inspector shows the returned creative direction, world rules,
characters, initial situation, review summary, and missing runnable
requirements. Project creation stays locked until the server marks the package
runnable. Discussion never writes canonical Markdown, and successful
finalization selects the created project before linking to the first turn.

The discussion panel is an adaptation of Jan's original thread UI, not a second
chat implementation. It directly composes Jan's `Conversation`,
`ConversationContent`, `ConversationScrollButton`, and `MessageItem`, together
with a controlled composer extracted from the visual and keyboard interaction
layer of Jan's `ChatInput`. Only the transport and message adapter are replaced:
submission history goes to the Python Story Engine and returns as Jan
`UIMessage` display data. Jan thread persistence, chat stores, and direct
Provider inference are intentionally not mounted in this flow.

This flow remains inside the Jan-based React application. Tauri owns desktop
lifecycle and the Jan model bridge, while the Python Story Engine owns story
domain state and delegates candidate generation to original Concordia through
that bridge. The UI does not add another provider, model runtime, or desktop
shell.

Workbench is a compact operational view: current world state, pending work,
latest event, active characters, and one primary "Advance next turn" action.
The right inspector is collapsible. Green means confirmed, amber means pending
or risky, and red means blocked or failed.
