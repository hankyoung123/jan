import { execFileSync } from 'node:child_process'
import { existsSync, readFileSync, rmSync } from 'node:fs'
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
    expect(packageJson.scripts.build).toBe(
      'yarn build:sidecar && yarn build:web && yarn build:tauri'
    )
    for (const platform of ['win32', 'darwin', 'linux']) {
      expect(packageJson.scripts[`build:tauri:${platform}`]).not.toContain(
        'yarn build:sidecar'
      )
    }
    expect(packageJson.scripts['build:tauri:darwin']).not.toContain(
      'universal-apple-darwin'
    )

    const cargoManifest = readFileSync(
      join(workspaceRoot, 'src-tauri/Cargo.toml'),
      'utf8'
    )
    expect(cargoManifest).not.toContain('name = "story-engine-cli"')
    expect(cargoManifest).not.toContain('name = "jan-cli"')

    for (const platform of ['.macos', '.windows', '.linux']) {
      const config = readJson(`src-tauri/tauri${platform}.conf.json`) as {
        bundle: { resources: Record<string, string> }
      }
      expect(
        Object.keys(config.bundle.resources).some((path) => path.includes('jan-cli'))
      ).toBe(false)
    }

    const activeNsisTemplate = join(
      workspaceRoot,
      'src-tauri/tauri.bundle.windows.nsis.template'
    )
    expect(existsSync(activeNsisTemplate)).toBe(false)

    const windowsConfig = readJson('src-tauri/tauri.windows.conf.json') as {
      bundle: {
        windows?: {
          nsis?: { template?: string }
        }
      }
    }
    expect(windowsConfig.bundle.windows?.nsis?.template).toBeUndefined()

    const archivedNsisTemplate = readFileSync(
      join(
        workspaceRoot,
        'docs/upstream/jan/workflows-packaging/tauri.bundle.windows.nsis.template'
      ),
      'utf8'
    )
    expect(archivedNsisTemplate).toContain('story-engine-cli.exe')
    expect(archivedNsisTemplate).not.toContain('oname=jan-cli.exe')
  })

  it('keeps updater controls disabled only for development builds', () => {
    const config = readJson('src-tauri/tauri.conf.json') as {
      build: {
        beforeDevCommand: string
        beforeBuildCommand: string
      }
    }

    expect(config.build.beforeDevCommand).toContain(
      'AUTO_UPDATER_DISABLED=true'
    )
    expect(config.build.beforeBuildCommand).not.toContain(
      'AUTO_UPDATER_DISABLED=true'
    )
  })

  it('provides a release build entrypoint that consumes the generated updater config', () => {
    const packageJson = readJson('package.json') as {
      scripts: Record<string, string>
    }

    expect(packageJson.scripts['build:tauri:release']).toContain(
      'prepare:release'
    )
    expect(packageJson.scripts['build:tauri:release']).toContain(
      'run-script-os'
    )
    for (const platform of ['win32', 'linux', 'darwin']) {
      expect(packageJson.scripts[`build:tauri:release:${platform}`]).toContain(
        '--config .build/tauri.release.conf.json'
      )
    }
  })

  it('generates a signed release updater config without changing the source config', () => {
    const packageJson = readJson('package.json') as {
      scripts: Record<string, string>
    }
    expect(packageJson.scripts['prepare:release']).toContain(
      'prepare-release-config.mjs'
    )

    const output = join(workspaceRoot, '.build/test-tauri-release-config.json')
    const source = readFileSync(
      join(workspaceRoot, 'src-tauri/tauri.conf.json'),
      'utf8'
    )
    try {
      execFileSync(
        'node',
        ['scripts/prepare-release-config.mjs', '--output', output],
        {
          cwd: workspaceRoot,
          env: {
            ...process.env,
            STORY_ENGINE_VERSION: '0.2.0',
            STORY_ENGINE_UPDATER_ENDPOINT:
              'https://updates.story-engine.example/latest.json',
            TAURI_UPDATER_PUBLIC_KEY: 'test-public-key',
          },
          stdio: 'pipe',
        }
      )
      const generated = readJson('.build/test-tauri-release-config.json') as {
        version: string
        plugins: { updater: { endpoints: string[]; pubkey: string } }
        bundle: { createUpdaterArtifacts: boolean }
      }
      expect(generated.version).toBe('0.2.0')
      expect(generated.plugins.updater.endpoints).toEqual([
        'https://updates.story-engine.example/latest.json',
      ])
      expect(generated.plugins.updater.pubkey).toBe('test-public-key')
      expect(generated.bundle.createUpdaterArtifacts).toBe(true)
      expect(readFileSync(join(workspaceRoot, 'src-tauri/tauri.conf.json'), 'utf8')).toBe(
        source
      )
    } finally {
      rmSync(output, { force: true })
    }
  })

  it('rejects an insecure updater endpoint before writing release config', () => {
    const output = join(workspaceRoot, '.build/test-invalid-tauri-release-config.json')
    rmSync(output, { force: true })
    expect(() =>
      execFileSync(
        'node',
        ['scripts/prepare-release-config.mjs', '--output', output],
        {
          cwd: workspaceRoot,
          env: {
            ...process.env,
            STORY_ENGINE_VERSION: '0.2.0',
            STORY_ENGINE_UPDATER_ENDPOINT: 'http://updates.example.com/latest.json',
            TAURI_UPDATER_PUBLIC_KEY: 'test-public-key',
          },
          stdio: 'pipe',
        }
      )
    ).toThrow()
    expect(existsSync(output)).toBe(false)
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
