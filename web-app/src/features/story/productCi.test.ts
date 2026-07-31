import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const workspaceRoot = existsSync(resolve(process.cwd(), 'web-app'))
  ? process.cwd()
  : resolve(process.cwd(), '..')

describe('Story Engine CI contract', () => {
  it('owns one product quality workflow instead of the inherited Jan linter', () => {
    const workflowPath = join(
      workspaceRoot,
      '.github/workflows/story-engine-ci.yml'
    )

    expect(existsSync(workflowPath)).toBe(true)
    expect(
      existsSync(
        join(workspaceRoot, '.github/workflows/jan-linter-and-test.yml')
      )
    ).toBe(false)

    const workflow = readFileSync(workflowPath, 'utf8')
    expect(workflow).toContain('name: Story Engine CI')
    expect(workflow).toContain('permissions:\n  contents: read')

    for (const job of ['web:', 'story-engine:', 'contracts:', 'desktop:']) {
      expect(workflow).toContain(job)
    }

    for (const command of [
      'yarn install --immutable',
      'yarn lint:jan',
      'yarn test:jan',
      'yarn playwright install --with-deps chromium',
      'yarn test:visual',
      'yarn build:web',
      'uv sync --frozen --project apps/story-engine --extra dev',
      'ruff check apps/story-engine/src apps/story-engine/tests',
      'mypy --config-file apps/story-engine/pyproject.toml',
      'pytest -c apps/story-engine/pyproject.toml apps/story-engine/tests',
      'python -m build apps/story-engine',
      'uv sync --frozen --project apps/story-engine --extra packaging',
      'apps/story-engine/packaging/story-engine.spec',
      'scripts/smoke_story_engine_sidecar.py',
      'yarn contracts:check',
      'rustfmt --check --edition 2021',
      'src-tauri/src/core/story_engine_runtime.rs',
      'src-tauri/src/core/story_model_bridge.rs',
      'cargo test --manifest-path src-tauri/Cargo.toml',
      'story_engine_runtime::tests --lib',
      'cargo clippy',
      'yarn tauri build --no-bundle',
    ]) {
      expect(workflow, command).toContain(command)
    }

    expect(workflow).not.toContain('rm -rf ~/jan')
    expect(workflow).not.toContain('janhq/jan/releases')
    expect(workflow).not.toContain('cargo fmt --check')
  })
})
