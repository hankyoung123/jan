# Phase 9 Evidence Audit

This audit maps the Phase 9 RAG V1 requirements in `docs/product-plan.md` to
the current Python Story Engine implementation, Jan-based React UI, and
executable evidence.

## Result

Phase 9 is complete against its seven deliverables and three written
acceptance criteria. The derived index is rebuilt from canonical Markdown,
authorization is applied before retrieval scoring, and every RAG fragment
injected into Writer or Editor carries stable source metadata.

The implementation remains inside the architecture boundary established by
ADR-0004: Python owns story-domain retrieval, while Writer and Editor inference
continues through Jan's existing `ModelGateway`. No Provider, API-key, model
loading, or second RAG settings stack was added.

## Deliverables

| Requirement | Status | Direct implementation evidence | Executable evidence |
|---|---|---|---|
| Markdown chunking | Complete | `RagService._document_paths` discovers canonical project, world, character, Event, scene, and source Markdown. `_chunks_for` parses Front Matter, headings, and bounded body chunks, and assigns deterministic unique chunk IDs. | `test_markdown_index_supports_exact_id_and_bm25_with_citations`; `RagIndex.count_matches_chunks` rejects duplicate IDs or inconsistent counts. |
| Exact ID retrieval | Complete | `RagService.search` resolves either an exact `chunk_id` or all authorized chunks for a `source_id`, after applying the requested scope. | The RAG service and protected API tests retrieve `event-000001` and verify exact-mode citations while excluding its hidden result. |
| BM25 | Complete | `_tokens` supports Latin terms plus Chinese characters and bigrams; `_bm25` computes length-normalized BM25 scores with deterministic score/chunk-ID ordering and a small title match boost. | `test_markdown_index_supports_exact_id_and_bm25_with_citations` proves the confirmed Event ranks first for a Chinese query. |
| Scope permission filtering | Complete | `_authorized` implements Editorial, Writer, and Character scopes. `search` constructs the authorized corpus before either exact lookup or BM25 document frequencies and scoring. Writer can access confirmed public Events, scenes, safe world sections, project direction, and the explicit `source:style` guide, but not arbitrary sources. | `test_character_scope_filters_unauthorized_markdown_before_scoring` proves participant, hidden-result, and other-character-card isolation; the Writer test proves style access while rejecting `source:research`; API tests cover authentication and invalid Character scope. |
| Retrieval evidence view | Complete | `SceneDraft.retrieval_evidence` stores derived Writer and Editor citations. The manuscript Inspector groups the evidence by task and displays score, source, heading, chunk ID, path, permission scope, and content; a shadcn icon button rebuilds the index. | `StoryViews.test.tsx` verifies Writer and Editor evidence, stable chunk metadata, and the protected rebuild action. |
| Index rebuild | Complete | `RagService.rebuild_index` writes `.story-engine/index/rag-v1.json` atomically. `ensure_index` rebuilds a missing, invalid, or fingerprint-stale index from canonical Markdown. `POST /projects/{project_id}/rag/rebuild` exposes an authenticated explicit rebuild. | `test_deleted_index_rebuilds_completely_from_canonical_markdown` deletes the derived index and compares every rebuilt chunk and fingerprint with the original; the API and manuscript UI tests cover explicit rebuild. |
| Writer and Editor retrieval | Complete | `ManuscriptService._writer_evidence` combines exact confirmed-Event evidence with scoped BM25 context; `_editor_evidence` retrieves under full Editorial scope. `GatewayManuscriptAgent` injects the evidence through the existing Writer and Editor profiles. | Manuscript service and API tests prove both tasks receive non-empty cited evidence and that both prompts include mandatory source metadata without exposing hidden Event results. |

## Acceptance Criteria

### Character Scope cannot return unauthorized content

Complete. Character filtering happens before exact lookup, document-frequency
calculation, and scoring. A Character can retrieve its own card, safe world
sections, and public sections of Events in which it participated. It cannot
retrieve another card, a non-participant Event, Event hidden results, arbitrary
sources, scenes, or project-wide content.

Direct evidence:

- `test_character_scope_filters_unauthorized_markdown_before_scoring`
- `test_rag_api_enforces_character_scope_and_authentication`
- `RetrievalScope.character_scope_has_exact_owner`

### Deleting the index permits a complete rebuild

Complete. The index contains only derived data. The deletion test removes the
entire `.story-engine/index` directory, triggers retrieval, and proves that the
new index has the same fingerprint and complete ordered chunk payload as the
index generated before deletion. Canonical Markdown is the only recovery
source.

### Every injected fragment has a source

Complete. `RetrievalEvidence` requires `chunk_id`, `source_type`, `source_id`,
`source_path`, `heading`, `permission_scope`, score, retrieval mode, content,
and consuming task. `_evidence_context` serializes those fields for every RAG
fragment passed to Writer or Editor. Service, API, and UI tests assert the
metadata at the prompt, response, persisted-derived-draft, and Inspector
boundaries.

## Canonical And Security Boundaries

- `.story-engine/index/rag-v1.json` is disposable derived state.
- Retrieval evidence is stored only with the derived `SceneDraft`; canonical
  scene Markdown contains no RAG payload.
- Scope authorization is deterministic Python domain logic and is never
  delegated to a model prompt.
- Writer cannot retrieve hidden Event results, character cards, world
  variables, or arbitrary source files.
- Editorial scope can inspect all canonical Markdown for review.
- Jan remains the sole owner of Provider configuration, credentials, model
  loading, and inference.
- The separately licensed Jan RAG backend was not copied; this index and
  retriever are Story Engine domain code.

## Verification

Focused verification on 2026-08-01:

- Python RAG, manuscript integration, and protected API: 16 tests passed.
- Web manuscript workspace and Novel editor: 23 tests passed.
- Ruff passed for the new RAG modules and tests.

Repository-wide verification on 2026-08-01:

- Jan Core, Web, and retained extensions: 290 test files and 3,277 tests passed.
- Python Story Engine: 114 tests passed; the only warning is the existing
  Starlette `TestClient`/`httpx` deprecation notice.
- Web and Python lint passed.
- mypy passed across 54 Python source files; generated contract TypeScript
  typechecking passed.
- Web production build passed with the existing Vite large-chunk advisories.
- Python sdist and wheel builds passed.
- The aggregate minimum-window Playwright test passed for all nine primary
  routes at `1024x740`.
- OpenAPI generation drift passed after the generated contract artifacts were
  staged.
