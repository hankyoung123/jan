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

  it('excludes the inherited Jan marketing site and promotional assets', () => {
    for (const path of [
      'JanBanner.png',
      'demo.gif',
      'README.ja.md',
      'README.zh.md',
      'docs/src',
      'docs/public',
      'docs/static',
      'docs/package.json',
      '.github/workflows/jan-docs.yml',
      '.github/workflows/clean-cloudflare-page-preview-url-and-r2.yml',
    ]) {
      expect(existsSync(join(workspaceRoot, path)), path).toBe(false)
    }

    for (const path of [
      'docs/product-plan.md',
      'docs/architecture.md',
      'docs/domain-model.md',
      'docs/data-contracts.md',
    ]) {
      expect(existsSync(join(workspaceRoot, path)), path).toBe(true)
    }

    const contributingGuide = readFileSync(
      join(workspaceRoot, 'CONTRIBUTING.md'),
      'utf8'
    )
    expect(contributingGuide).toContain('# Contributing to Story Engine')
    expect(contributingGuide).not.toContain('# Contributing to Jan')
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
            if (/\bJan\b|Janowi|\bJana\b|\bMenlo(?: Research)?\b/.test(value)) {
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

  it('does not leave an inherited Jan Flatpak manifest in the product packaging directory', () => {
    const flatpakReadme = readFileSync(
      join(workspaceRoot, 'flatpak/README.md'),
      'utf8'
    )
    expect(flatpakReadme).toContain('com.storyengine.desktop')
    expect(flatpakReadme).toMatch(/upstream\s+reference/)
    expect(existsSync(join(workspaceRoot, 'flatpak/ai.jan.Jan.yml'))).toBe(false)
    expect(existsSync(join(workspaceRoot, 'flatpak/ai.jan.Jan.metainfo.xml'))).toBe(
      false
    )
    expect(existsSync(join(workspaceRoot, 'flatpak/flathub.json'))).toBe(false)
  })

  it('keeps the shipped CLI help and provider examples product-branded', () => {
    const cli = readFileSync(
      join(workspaceRoot, 'src-tauri/src/bin/story-engine-cli.rs'),
      'utf8'
    )

    expect(cli).toContain('Story Engine')
    for (const marker of [
      'janhq/Jan-',
      'Jan data folder',
      "Jan's settings",
      'Jan.app',
      'jan provider',
      'jan/{model_id}',
      '"JAN" in ANSI Shadow',
    ]) {
      expect(cli, marker).not.toContain(marker)
    }

    expect(cli).toContain('story-engine/{model_id}')
  })

  it('does not expose inherited Jan names in active product copy', () => {
    const claudeCodeSettings = readFileSync(
      join(workspaceRoot, 'web-app/src/routes/settings/claude-code.tsx'),
      'utf8'
    )
    const browserExtensionHook = readFileSync(
      join(workspaceRoot, 'web-app/src/hooks/useJanBrowserExtension.ts'),
      'utf8'
    )
    const modelService = readFileSync(
      join(workspaceRoot, 'web-app/src/services/models/default.ts'),
      'utf8'
    )
    const mcpSettingsRoute = readFileSync(
      join(workspaceRoot, 'web-app/src/routes/settings/mcp-servers.tsx'),
      'utf8'
    )
    const mcpDefaults = readFileSync(
      join(workspaceRoot, 'src-tauri/src/core/mcp/constants.rs'),
      'utf8'
    )

    expect(claudeCodeSettings).not.toContain('Use Jan-Code')
    expect(browserExtensionHook).not.toContain("toast.success('Jan Browser MCP")
    expect(browserExtensionHook).not.toContain("toast.error('Jan Browser MCP")
    expect(browserExtensionHook).not.toContain("toast.warning('Jan Browser MCP")
    expect(modelService).not.toContain('latest Jan model')
    expect(mcpSettingsRoute).toContain('getMCPServerDisplayName')
    expect(mcpSettingsRoute).not.toContain('{key}</h1>')
    expect(mcpDefaults).toContain('"displayName": "Browser MCP"')
  })
})
