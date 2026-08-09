# Character Relationship Graph Design

> **Historical optional-view design.** A relationship graph is not current
> World Session MVP scope. The Living Story World PRD and ADR-0009 take
> precedence.

## Goal

Add the deferred complex character relationship graph to the Characters page.
The roster remains the default view; the graph is an optional view. Character
detail also exposes the previously missing relationship list.

## Current State

`Character.relationships` already exists in the contract as
`Relationship[]` (`character_id` plus description), but `CharactersView` does
not render it. The project snapshot is loaded from
`GET /projects/{project_id}`.

## Design

- Add a view toggle to `CharactersView`: `Character roster` (default) and
  `Relationship graph`.
- The roster keeps the existing character list and detail layout and adds a
  relationship section under the character details.
- The relationship graph renders all characters on a circle with SVG lines for
  deduplicated relationships. Active characters are green, ordinary characters
  amber, and retired characters neutral.
- Relationship descriptions are available as SVG hover titles and in the roster
  detail list.
- No backend changes and no new graph dependency are required for this first
  version.

## Data Flow

`ProjectSnapshot.characters` -> circular node positions -> deduplicated edges
-> SVG graph. The roster continues to use the existing detail rendering.

## Testing

- Component tests cover relationship rendering in the roster, the graph
  toggle, node names in the SVG, and the empty relationship state.
- Web lint, focused tests, and the production build must stay green.

## Non-Goals

- No relationship editing.
- No force-directed layout or dragging in this iteration.
- No release/signing/update work.
