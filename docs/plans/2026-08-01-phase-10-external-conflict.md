# Phase 10 External Markdown Conflict Audit

This audit covers the Phase 10 requirement for external Markdown modification
conflicts in `docs/product-plan.md`.

## Result

Complete for the canonical write boundary. The workspace index already detects
valid and invalid external edits. Formal Turn, manuscript, Event Amendment,
and NPC promotion candidates now retain the canonical `WorkspaceIndex.revision`
seen during review. Every `EventCommitService` write checks that revision
before preparing and immediately before committing its atomic batch. A missing,
invalid, or changed revision returns a domain version conflict and leaves the
formal Markdown and Event set untouched.

## Evidence

- `apps/story-engine/src/story_engine/workspace/session.py` computes the
  validated SHA-256 canonical revision.
- `apps/story-engine/src/story_engine/events/commit.py` enforces the revision
  for Turn, promotion, scene, and amendment commits.
- `apps/story-engine/tests/events/test_commit.py` covers external edits that
  preserve the semantic world or NPC version.
- `apps/story-engine/tests/manuscript/test_service.py` covers an external edit
  between scene generation and save.
- `apps/story-engine/tests/api/test_projects.py` verifies the authenticated
  confirmation endpoint returns `409` and does not append an Event.
- `apps/story-engine/tests/workspace/test_session.py` covers watcher refresh,
  invalid Markdown preservation, and repair notification.

## Verification

- Python Story Engine: 117 tests passed.
- Ruff and mypy passed.
- Generated OpenAPI and TypeScript contracts include the revision fields.
