import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { extname, join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const workspaceRoot = existsSync(resolve(process.cwd(), 'web-app'))
  ? process.cwd()
  : resolve(process.cwd(), '..')

function readJson(relativePath: string): unknown {
  return JSON.parse(readFileSync(join(workspaceRoot, relativePath), 'utf8'))
}

function stringValues(value: unknown): string[] {
  if (typeof value === 'string') return [value]
  if (Array.isArray(value)) return value.flatMap(stringValues)
  if (value && typeof value === 'object') {
    return Object.values(value).flatMap(stringValues)
  }
  return []
}

describe('Story Engine product branding', () => {
  it('uses the Story Engine title in every desktop window configuration', () => {
    for (const platform of ['macos', 'windows', 'linux']) {
      const config = readJson(
        `src-tauri/tauri.${platform}.conf.json`
      ) as { app: { windows: Array<{ title: string }> } }

      expect(config.app.windows[0]?.title).toBe('Story Engine')
    }

    const windowService = readFileSync(
      join(workspaceRoot, 'web-app/src/services/window/tauri.ts'),
      'utf8'
    )
    expect(windowService).not.toContain(' - Jan')

    const webShell = readFileSync(
      join(workspaceRoot, 'web-app/index.html'),
      'utf8'
    )
    expect(webShell).toContain('<title>Story Engine</title>')
    expect(webShell).toContain('alt="Story Engine"')
    expect(webShell).not.toContain('AI Story Evolution Engine')
  })

  it('ships no inherited Jan logo asset or reference', () => {
    const imageRoot = join(workspaceRoot, 'web-app/public/images')
    expect(existsSync(join(imageRoot, 'jan-logo.png'))).toBe(false)
    expect(existsSync(join(imageRoot, 'model-provider/jan.png'))).toBe(false)

    const sourceRoot = join(workspaceRoot, 'web-app/src')
    const pending = [sourceRoot]
    const sourceFiles: string[] = []
    while (pending.length > 0) {
      const current = pending.pop()!
      for (const entry of readdirSync(current, { withFileTypes: true })) {
        const path = join(current, entry.name)
        if (entry.isDirectory()) pending.push(path)
        else if (['.ts', '.tsx'].includes(extname(entry.name))) sourceFiles.push(path)
      }
    }

    for (const path of sourceFiles) {
      if (path.endsWith('productBranding.test.ts')) continue
      expect(readFileSync(path, 'utf8')).not.toContain('/images/jan-logo.png')
      expect(readFileSync(path, 'utf8')).not.toContain('/images/model-provider/jan.png')
    }
  })

  it('contains no inherited Jan product name or Menlo attribution in localized copy', () => {
    const localeRoot = join(workspaceRoot, 'web-app/src/locales')
    const pending = [localeRoot]
    const violations: string[] = []

    while (pending.length > 0) {
      const current = pending.pop()!
      for (const entry of readdirSync(current, { withFileTypes: true })) {
        const path = join(current, entry.name)
        if (entry.isDirectory()) {
          pending.push(path)
        } else if (entry.name.endsWith('.json')) {
          for (const value of stringValues(
            JSON.parse(readFileSync(path, 'utf8'))
          )) {
            if (/\bJan\b|\bMenlo(?: Research)?\b/.test(value)) {
              violations.push(`${path}: ${value}`)
            }
          }
        }
      }
    }

    expect(violations).toEqual([])
  })

  it('uses Story Engine paths when repackaging the Linux AppImage', () => {
    const script = readFileSync(
      join(workspaceRoot, 'src-tauri/build-utils/buildAppImage.sh'),
      'utf8'
    )
    expect(script).toContain('APP_NAME="Story Engine"')
    expect(script).toContain('${APP_NAME}.AppDir')
    expect(script).toContain('rm -f -- "${APP_IMAGE}"')
    expect(script).not.toContain('$(ls ')
    expect(script).not.toContain('/Jan')
  })
})
