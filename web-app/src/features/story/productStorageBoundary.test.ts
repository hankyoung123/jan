import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const workspaceRoot = existsSync(resolve(process.cwd(), 'web-app'))
  ? process.cwd()
  : resolve(process.cwd(), '..')

function trackedFiles(): string[] {
  return execFileSync('git', ['ls-files', '-z'], {
    cwd: workspaceRoot,
    encoding: 'utf8',
  })
    .split('\0')
    .filter(Boolean)
    .filter((path) => existsSync(join(workspaceRoot, path)))
}

describe('Story Engine storage boundary', () => {
  it('keeps canonical Story project code and fixtures database-free', () => {
    const storyOwnedPrefixes = [
      'apps/story-engine/',
      'packages/contracts/',
      'web-app/src/editor/',
      'web-app/src/features/story/',
    ]
    const databaseArtifacts = trackedFiles().filter(
      (path) =>
        storyOwnedPrefixes.some((prefix) => path.startsWith(prefix)) &&
        /\.(?:db|sqlite|sqlite3)$/i.test(path)
    )

    expect(databaseArtifacts).toEqual([])

    const pyproject = readFileSync(
      join(workspaceRoot, 'apps/story-engine/pyproject.toml'),
      'utf8'
    )
    expect(pyproject).not.toMatch(/\b(?:sqlalchemy|duckdb|sqlite-utils)\b/i)

    const storyPython = trackedFiles().filter(
      (path) =>
        path.startsWith('apps/story-engine/src/story_engine/') &&
        path.endsWith('.py')
    )
    for (const path of storyPython) {
      expect(readFileSync(join(workspaceRoot, path), 'utf8'), path).not.toMatch(
        /^\s*(?:from|import)\s+(?:sqlite3|sqlalchemy|duckdb)\b/m
      )
    }
  })

  it('keeps inherited Jan SQLite limited to mobile thread infrastructure', () => {
    const helper = readFileSync(
      join(workspaceRoot, 'src-tauri/src/core/threads/helpers.rs'),
      'utf8'
    )
    expect(helper).toContain('cfg!(any(target_os = "android", target_os = "ios"))')
    expect(existsSync(join(workspaceRoot, 'src-tauri/src/core/threads/db.rs'))).toBe(
      true
    )

    const storyEngineSources = trackedFiles().filter((path) =>
      path.startsWith('apps/story-engine/')
    )
    expect(storyEngineSources.some((path) => path.includes('/migrations/'))).toBe(
      false
    )
  })
})
