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

## Streaming event envelope

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

## Contract generation

The engine exports OpenAPI deterministically. The root verification command
fails when generated OpenAPI or TypeScript types differ from committed files.
Secrets, provider keys, and sidecar tokens are never represented in response
schemas.

