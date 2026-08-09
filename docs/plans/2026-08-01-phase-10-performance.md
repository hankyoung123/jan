# Phase 10 Large-Project Performance Audit

> **Historical performance audit.** Measurements may remain useful, but this
> audit does not define or prove current World Session MVP completion.

This audit covers the Phase 10 large-project performance requirement and the
V1 requirement for at least 30 consecutive rounds.

## Scenario

`test_large_project_supports_thirty_traceable_rounds` creates a valid project
with 500 canonical, append-only Event Markdown files, aligns the current World
and Character versions to that history, then generates and confirms 30 further
rounds through the normal `EvolutionService` and `EventCommitService` paths.
The test checks every resulting Event sequence, source Turn, World version,
World round variable, Character version, and last-event link.

The test also measures the 30-round execution and fails above 60 seconds. The
budget is intentionally broad enough for hosted CI filesystem variance while
still catching an accidental unbounded regression in canonical indexing or
event loading.

## Verification

- Performance scenario: passed, 1 test deselected 11 other evolution tests.
- Local command wall time: `7.07s` including `uv` and pytest startup; the test
  call itself took `6.88s`.
- Full Python Story Engine suite after adding the scenario: 119 tests passed;
  the only warning is the existing Starlette `TestClient`/`httpx` deprecation.
