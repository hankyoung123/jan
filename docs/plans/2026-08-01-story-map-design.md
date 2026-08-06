# Story Map Optional View Design

## Goal

The event history page keeps the timeline as its default view and adds a
Story Map as an optional view. The Story Map is not a homepage and does not
replace the canonical Markdown/event timeline.

## Current State

`EventsView` currently renders a static four-row timeline and does not load a
project's real events. The Story Engine already exposes
`GET /projects/{project_id}/events`, returning ordered `StoryEvent` records
with sequence, timestamp, participants, summary, approval state, world
changes, and public/hidden results.

## Design

- `EventsView` loads events through `engineRequest` for the active project and
  shows loading, empty, and error states.
- A segmented control switches between `Timeline` (default) and `Story Map`.
- The timeline keeps the existing vertical list, rendered from real events.
- The Story Map is a horizontal canvas of event nodes connected by lines.
  Turning points (events with world or character changes) are emphasized, and
  unconfirmed events use the amber visual state.
- No backend changes and no new graph dependency are required for this first
  version; the map is built from existing event data with CSS and lucide icons.

## Data Flow

`useActiveStoryProjectId` -> `engineRequest<StoryEvent[]>` -> ordered events ->
`Timeline` or `StoryMap` presentation component.

## Testing

- Component tests cover loading, empty, error, timeline rendering, Story Map
  rendering, and the view toggle.
- The existing Web test suite and lint/build gates must stay green.

## Non-Goals

- No new backend API.
- No editing or branching semantics in this iteration.
- No release/signing/update work.
