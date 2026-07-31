# Phase 8 Evidence Audit

This audit maps the Phase 8 Writer and manuscript requirements in
`docs/product-plan.md` to current implementation and executable evidence.

## Result

Phase 8 is complete against its seven deliverables and two written acceptance
criteria. Writer output remains derived until fact review succeeds, prose is
edited through Novel while Markdown remains canonical, and new facts cannot
enter formal state without an explicit Event Amendment confirmation.

## Deliverables

| Requirement | Status | Direct implementation evidence | Executable evidence |
|---|---|---|---|
| Generate scenes from Events | Complete | `ManuscriptService.generate_scene` resolves the requested sources through `_confirmed_events`, calls the Writer through the existing Jan-owned `ModelGateway`, reviews the output, and persists a derived `SceneDraft` | `test_writer_uses_only_confirmed_events_and_creates_derived_draft`; `test_scene_api_generates_lists_and_saves_reviewed_markdown`; `StoryViews.test.tsx` covers generation from the latest unrepresented confirmed Event |
| Novel editor | Complete | `NovelManuscriptEditor.tsx` uses Novel `EditorRoot`, `EditorContent`, `StarterKit`, and `Placeholder`; its shadcn toolbar exposes formatting commands and emits Markdown through the explicit conversion boundary | `NovelManuscriptEditor.test.tsx` proves supported formatting round-trips and editor changes publish Markdown |
| Chapter and scene list | Complete | `ManuscriptView` groups ordered `SceneDraft` records by `chapter_id`, renders chapter sections and scenes, and prevents navigation away from an unsaved edit | `StoryViews.test.tsx` proves scenes are grouped under visible chapter labels and remain selectable |
| Source Events | Complete | Every `Scene` and `SceneDraft` stores `source_event_ids`; the manuscript Inspector resolves and displays only matching confirmed Events and can be collapsed | Service source-boundary tests plus `StoryViews.test.tsx` source Inspector and collapse coverage |
| Fact difference detection | Complete | `GatewayManuscriptAgent.review` compares prose with confirmed Events and public fact IDs using the Editor profile; `ManuscriptReviewOutput` makes new facts explicit and blocking | `test_reviewed_user_edit_saves_canonical_scene_markdown`; `test_new_user_fact_requires_amendment_before_atomic_formal_commit`; API and UI review tests |
| Event Amendment | Complete | `update_scene` persists an `EventAmendmentCandidate` instead of formal prose when new facts exist; `confirm_amendment` validates the exact draft revision and delegates the atomic formal commit to `EventCommitService` | `test_new_user_fact_requires_amendment_before_atomic_formal_commit`; `test_scene_api_requires_explicit_amendment_confirmation`; `StoryViews.test.tsx` proves Canon is unchanged before confirmation |
| Markdown export | Complete | `ManuscriptService.export_markdown` emits canonical scenes in sequence and the UI downloads the returned `.md` artifact | `test_markdown_export_contains_saved_scenes_in_sequence`; `StoryViews.test.tsx` verifies the Markdown download |

## Acceptance Criteria

### Writer cannot generate unconfirmed Canon

Complete. `_confirmed_events` rejects missing, duplicate, unknown, or
unapproved Event sources before the Writer is called. A generated scene is
stored only in `SceneDraftStore`; `SceneStore` is written later through the
version-checked commit path after fact review. The following tests prove the
boundary directly:

- `test_writer_uses_only_confirmed_events_and_creates_derived_draft`
- `test_writer_rejects_unknown_or_unconfirmed_event_sources`
- `test_scene_api_generates_lists_and_saves_reviewed_markdown`
- `saves a grounded Writer draft without requiring a no-op edit`

### User-added facts produce an Amendment candidate

Complete. A review containing `new_facts` creates a pending
`EventAmendmentCandidate`, leaves canonical scene, world, and Event Markdown
unchanged, and requires the explicit confirmation endpoint. Confirmation
atomically commits the scene, world fact changes, append-only Event, and
Amendment state. The following tests prove both sides of the boundary:

- `test_new_user_fact_requires_amendment_before_atomic_formal_commit`
- `test_scene_api_requires_explicit_amendment_confirmation`
- `keeps new facts out of Canon until the user confirms the Amendment`

## Concurrency And Recovery Guards

Phase 8 also preserves the earlier formal-state rules:

- draft revisions and canonical scene versions are checked independently;
- stale writes cannot alter formal Markdown;
- an Amendment must still match the exact pending scene draft revision;
- saved scenes are reloaded from canonical storage;
- export reads canonical `SceneStore` records rather than unsaved drafts.

These rules are covered by
`test_stale_scene_revision_never_writes_formal_markdown`,
`test_stale_canonical_scene_version_cannot_overwrite_saved_prose`, and
`test_scene_api_rejects_stale_revision_without_formal_write`.

## Verification

Focused verification on 2026-08-01:

- Web manuscript workspace and Novel editor: 22 tests passed.
- Python manuscript service and API: 10 tests passed.
- Web lint passed.
- The running `/manuscript` route rendered at `1024x740` without document,
  body, or main overflow; data-state interaction remains covered by component
  tests because the browser profile had no active story project.

Repository-wide verification on 2026-08-01:

- `yarn lint:jan`: passed.
- `yarn test:jan`: passed across Core, Web, and retained extensions; the two
  new manuscript regressions are included in the Web suite.
- `yarn test:visual`: the aggregate minimum-window test passed for all nine
  primary routes at `1024x740`.
- `yarn build:web`: passed. Vite retained its existing large-chunk advisory.
- Ruff: passed.
- mypy: passed across 50 Python source files.
- Python Story Engine: 108 tests passed. The only warning is the existing
  Starlette `TestClient`/`httpx` deprecation notice.
- OpenAPI generation drift and generated TypeScript contract typecheck:
  passed.
