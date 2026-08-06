# Jan Upstream Patch Ledger

Story Engine is based on Jan `v0.8.4` at
`5f30aee467f08941964a83f946e2663e7ae0e01f`. Upstream changes are reviewed and
cherry-picked deliberately; `upstream/main` is never merged wholesale because
the product removes Jan chat, assistant, RAG, and brand domains.

The scheduled `Story Engine upstream audit` workflow runs
`scripts/report-jan-upstream.sh` every Monday. Its job summary groups upstream
commits touching Tauri/updater code, model runtimes/providers, and dependency or
security automation. Reviewers must also inspect the complete commit list.

For every accepted patch, add a row before merging it:

| Upstream commit | Local commit | Area | Decision and verification |
|---|---|---|---|
| _None recorded_ | - | - | Initial ledger created against the locked v0.8.4 baseline |

Rejected patches that are security- or runtime-relevant must be recorded below
with the reason and replacement mitigation. Routine feature commits outside the
retained product boundary do not require individual rejection entries.
