import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const workspaceRoot = existsSync(resolve(process.cwd(), 'web-app'))
  ? process.cwd()
  : resolve(process.cwd(), '..')

function readJson(relativePath: string): Record<string, unknown> {
  return JSON.parse(
    readFileSync(join(workspaceRoot, relativePath), 'utf8')
  ) as Record<string, unknown>
}

describe('Story Engine packaged Sidecar boundary', () => {
  it('builds a PyInstaller onedir artifact and smoke-tests the frozen API', () => {
    const packageJson = readJson('package.json') as {
      scripts: Record<string, string>
    }
    const pyproject = readFileSync(
      join(workspaceRoot, 'apps/story-engine/pyproject.toml'),
      'utf8'
    )
    const spec = readFileSync(
      join(workspaceRoot, 'apps/story-engine/packaging/story-engine.spec'),
      'utf8'
    )
    const smoke = readFileSync(
      join(workspaceRoot, 'scripts/smoke_story_engine_sidecar.py'),
      'utf8'
    )

    expect(pyproject).toContain('pyinstaller>=6.16.0,<7')
    expect(packageJson.scripts['build:sidecar']).toContain('pyinstaller')
    expect(packageJson.scripts['build:sidecar']).toContain(
      '--distpath src-tauri/resources'
    )
    expect(packageJson.scripts['test:sidecar']).toContain(
      'smoke_story_engine_sidecar.py'
    )
    expect(spec).toContain('exclude_binaries=True')
    expect(spec).toContain('bundle = COLLECT(')
    expect(spec).not.toContain('--onefile')
    expect(smoke).toContain('/health')
    expect(smoke).toContain('/api/status')
    expect(smoke).toContain('ProxyHandler({})')
  })

  it('maps the onedir artifact into every desktop bundle', () => {
    for (const platform of ['', '.macos', '.windows', '.linux']) {
      const config = readJson(`src-tauri/tauri${platform}.conf.json`) as {
        bundle: { resources: Record<string, string> }
      }
      expect(config.bundle.resources['resources/story-engine/']).toBe(
        'story-engine/'
      )
    }

    const runtime = readFileSync(
      join(workspaceRoot, 'src-tauri/src/core/story_engine_runtime.rs'),
      'utf8'
    )
    expect(runtime).toContain('.join("story-engine")')
    expect(runtime).toContain('"story-engine.exe"')

    const packageJson = readJson('package.json') as {
      scripts: Record<string, string>
    }
    for (const platform of ['win32', 'darwin', 'linux']) {
      expect(packageJson.scripts[`build:tauri:${platform}`]).toContain(
        'yarn build:sidecar'
      )
    }
    expect(packageJson.scripts['build:tauri:darwin']).not.toContain(
      'universal-apple-darwin'
    )

    const makefile = readFileSync(join(workspaceRoot, 'Makefile'), 'utf8')
    expect(makefile).toContain(
      'cp src-tauri/resources/bin/jan-cli src-tauri/target/release/jan-cli'
    )
  })

  it('keeps generated Sidecar binaries out of source control', () => {
    const tracked = execFileSync(
      'git',
      ['ls-files', 'src-tauri/resources/story-engine'],
      { cwd: workspaceRoot, encoding: 'utf8' }
    )
    const gitignore = readFileSync(join(workspaceRoot, '.gitignore'), 'utf8')

    expect(tracked).toBe('')
    expect(gitignore).toContain('src-tauri/resources/')
  })
})
