# Data Contracts

## API conventions

- JSON fields use `snake_case`.
- Identifiers are stable lower-case strings or ULIDs.
- Timestamps are ISO-8601 UTC values.
- Validation errors use FastAPI problem details.
- Domain conflicts use HTTP `409`; missing resources use `404`.
- Authenticated requests use `Authorization: Bearer <session-token>`.

## Health

`GET /health` returns:

```json
{
  "status": "ok",
  "service": "story-engine",
  "version": "0.1.0"
}
```

## Submission and evolution

`POST /projects/{project_id}/submission/messages` accepts a non-canonical
`SubmissionDraft` plus user/assistant discussion history. Python invokes the
Editor task profile through the Jan model bridge, validates the structured
draft and submission review, and independently reports missing runnable
requirements. The path identifier and draft identifier must match. This route
does not create a project directory or write canonical Markdown.

`POST /submissions/finalize` validates a complete initial setting package and
creates the canonical Markdown project. The package contains creative
direction, world rules, a concrete incident and pressure, and two to four
active characters with explicit private fact identifiers. It has no outline
or future plot contract.

## Workspace lifecycle

`GET /projects` lists valid Markdown projects and whether each has an open
process-local workspace. It ignores directories that cannot validate as a
project; it never repairs canonical Markdown implicitly.

```text
POST /projects/{project_id}/open
GET  /projects/{project_id}/workspace
POST /projects/{project_id}/close
```

Open performs interrupted-transaction recovery before validating Markdown,
recreates missing derived cache/index directories, builds the in-memory index,
and starts the file watcher. Close stops that watcher and discards only the
in-memory session. It never deletes project files. Finalizing a submission
opens the new project automatically.

`WorkspaceIndex.revision` is a SHA-256 digest over ordered canonical relative
paths and their content hashes. Its document entries cover only `project.md`,
`world.md`, `characters/**/*.md`, `events/*.md`, and `scenes/*.md`. The JSON
copy below `.story-engine/index/project.json` is derived and rebuildable.
Turn, manuscript, amendment, and promotion candidates copy this revision into
`base_workspace_revision`. Formal confirmation rejects a missing or changed
revision with `409`, including an external edit that kept a document's
semantic version unchanged.

`POST /projects/{project_id}/turns/generate` asks the engine to assemble one
private context per participant, generate isolated character intents, resolve
one world outcome, and run the Editor review. The resulting candidate is
derived state below `.story-engine/turns`.

The Resolver receives a minimal existing-character roster and must prefer a
plausible existing person over creating a new one. Any `outcome.new_npcs` item
is still derived candidate data. Its lower-case, path-safe identifier must be
unique within the outcome and must not collide with a Character already in the
project. NPC candidates are not included in active Character contexts.

`TurnCandidate.base_character_versions` contains every Character present when
generation starts. Review and commit reject a participant or changed Character
without a baseline version, and commit rejects any version that no longer
matches canonical Markdown.

Only one generation may run per project. While it is running, React may call:

```text
POST /projects/{project_id}/turns/active/cancel
```

The returned `TurnCancellationResult` identifies the active Turn. Cancellation
propagates through Concordia into the Jan model request and produces
`turn.cancelled`; it does not write a candidate or canonical file. A late
request after generation has claimed completion returns `409`. Failed or
cancelled work is retried by invoking the same `turns/generate` boundary again.

The client cannot submit its own intents, outcomes, or review verdicts. After
generation it can invoke only these decision endpoints:

```text
POST /projects/{project_id}/turns/{turn_id}/request-revision
POST /projects/{project_id}/turns/{turn_id}/confirm
POST /projects/{project_id}/turns/{turn_id}/discard
```

Only `confirm` can reach `EventCommitService` and mutate canonical Markdown.
Revision sends the current world, existing Character roster, original intents,
previous outcome, and user instruction through the Resolver again. It replaces
the derived outcome, invalidates the old review, and runs a new Editor review;
discard changes only the derived candidate lifecycle. A Resolver or Editor
failure leaves the previous candidate unchanged.

When a confirmed outcome contains NPC candidates, `confirm` creates
`characters/npc/{npc_id}.md` together with the World, participant Character,
and append-only Event updates in one recoverable atomic batch. The commit
boundary repeats the identifier-collision check so a forged passing review
cannot overwrite an existing Character. An unconfirmed, revised, discarded,
cancelled, or failed Turn never creates NPC Markdown.

## Characters and promotion

```text
GET  /projects/{project_id}/characters
GET  /projects/{project_id}/characters/{character_id}
POST /projects/{project_id}/characters/{character_id}/promotion-review
POST /projects/{project_id}/characters/{character_id}/promote
```

`promotion-review` accepts only a canonical NPC. The Editor may return a
versioned, derived `PromotionCandidate`, but the Character remains an NPC and no
Event is written. A later `promote` request contains only the candidate ID. The
engine loads that exact candidate, requires it to be pending and still match the
NPC version, then atomically moves the Character Markdown to
`characters/active`, applies the reviewed goal, appends a user-approved Event,
and marks the derived candidate committed. Repeated confirmation or a stale
version returns a conflict without a partial canonical write.

## Streaming event envelope

Clients connect to `GET /ws/events?project_id={project_id}` with the WebSocket
subprotocols `story-engine.v1` and `story-engine.token.{session-token}`. The
token is never placed in the URL. Missing, duplicate, or invalid token
protocols are rejected with close code `1008`.

```json
{
  "event_id": "01J...",
  "project_id": "fog-harbor",
  "turn_id": "01J...",
  "timestamp": "2026-07-31T04:00:00Z",
  "type": "character.intent.completed",
  "payload": {}
}
```

The initial event type set is defined in `docs/product-plan.md` section 13.2.
Unknown event types must be ignored by clients for forward compatibility.
The bounded in-memory stream drops the oldest queued event for a slow client;
canonical state remains available through HTTP and Markdown reload.

`workspace.changed` uses `turn_id: "workspace"`. A ready payload contains
`changed_paths`, `revision`, and `world_version`. An error payload contains a
safe validation message. The bearer token remains in the WebSocket subprotocol,
never in its URL.

## Model profiles

`GET /models/profiles` returns the five application-level task routes.
`PUT /models/profiles/{profile_id}` updates only non-sensitive routing and limit
data. The Model Center uses Jan's in-memory Provider/model catalog for selectors
and sends only the `ModelProfile` contract to Python. Provider settings and API
keys are neither accepted nor returned by these endpoints. Story projects
reference profile IDs and task overrides only.

`GET /models/usage` returns process-lifetime request, prompt-token,
completion-token, and total-token counters. These totals are operational data,
not canonical project state.

`POST /models/complete` and `POST /models/stream` share the `ModelRequest`
contract. Structured calls include a JSON Schema string and fail with the
stable `structured_output_invalid` code when schema parsing, JSON parsing, or
validation fails. Streaming uses server-sent event records and applies the same
final validation before accounting the request as complete.

## Contract generation

The engine exports OpenAPI deterministically. The root verification command
fails when generated OpenAPI or TypeScript types differ from committed files.
Secrets, provider keys, and sidecar tokens are never represented in response
schemas.
