#!/usr/bin/env bash
set -euo pipefail

baseline='5f30aee467f08941964a83f946e2663e7ae0e01f'
target_ref="${1:-upstream/main}"

git rev-parse --verify "${baseline}^{commit}" >/dev/null
git rev-parse --verify "${target_ref}^{commit}" >/dev/null

commit_count="$(git rev-list --count "${baseline}..${target_ref}")"

printf '# Jan upstream audit\n\n'
printf -- '- Baseline: `%s` (Jan v0.8.4)\n' "$baseline"
printf -- '- Target: `%s`\n' "$target_ref"
printf -- '- Commits to assess: **%s**\n\n' "$commit_count"

report_category() {
  local title="$1"
  shift
  printf '## %s\n\n' "$title"
  local entries
  entries="$(git log --no-merges --date=short --pretty='- `%h` %ad %s' \
    "${baseline}..${target_ref}" -- "$@")"
  if [[ -n "$entries" ]]; then
    printf '%s\n\n' "$entries"
  else
    printf '_No path-matched commits._\n\n'
  fi
}

report_category 'Tauri, updater, and desktop runtime' \
  src-tauri/ package.json yarn.lock
report_category 'Model runtimes and providers' \
  core/ extensions/llamacpp-extension/ extensions/mlx-extension/ \
  extensions/download-extension/
report_category 'Build, dependency, and security automation' \
  .github/ Cargo.lock package.json yarn.lock

printf '## All upstream commits\n\n'
git log --no-merges --date=short --pretty='- `%h` %ad %s' \
  "${baseline}..${target_ref}" --max-count=200
printf '\n'
