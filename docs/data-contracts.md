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

`POST /submissions/finalize` validates a complete initial setting package and
creates the canonical Markdown project. The package contains creative
direction, world rules, a concrete incident and pressure, and two to four
active characters with explicit private fact identifiers. It has no outline
or future plot contract.

`POST /projects/{project_id}/turns/generate` asks the engine to assemble one
private context per participant, generate isolated character intents, resolve
one world outcome, and run the Editor review. The resulting candidate is
derived state below `.story-engine/turns`.

The client cannot submit its own intents, outcomes, or review verdicts. After
generation it can invoke only these decision endpoints:

```text
POST /projects/{project_id}/turns/{turn_id}/request-revision
POST /projects/{project_id}/turns/{turn_id}/confirm
POST /projects/{project_id}/turns/{turn_id}/discard
```

Only `confirm` can reach `EventCommitService` and mutate canonical Markdown.
Revision replaces the derived outcome and reruns review; discard changes only
the derived candidate lifecycle.

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

## Model profiles

`GET /models/profiles` returns the five application-level task routes.
`PUT /models/profiles/{profile_id}` updates only non-sensitive routing and limit
data. Provider writes may contain a write-only `api_key`; Provider reads return
`has_api_key` and never return the credential. Story projects reference profile
IDs and task overrides only.

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
