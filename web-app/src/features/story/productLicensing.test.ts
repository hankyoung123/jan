import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const workspaceRoot = existsSync(resolve(process.cwd(), 'web-app'))
  ? process.cwd()
  : resolve(process.cwd(), '..')

const agplPackagePaths = [
  'core/package.json',
  'extensions/assistant-extension/package.json',
  'extensions/download-extension/package.json',
  'extensions/llamacpp-extension/package.json',
  'extensions/mlx-extension/package.json',
]

const desktopTauriConfigs = [
  'src-tauri/tauri.conf.json',
  'src-tauri/tauri.macos.conf.json',
  'src-tauri/tauri.windows.conf.json',
  'src-tauri/tauri.linux.conf.json',
]

function readText(relativePath: string): string {
  return readFileSync(join(workspaceRoot, relativePath), 'utf8')
}

describe('Story Engine license inventory', () => {
  it('ships the exact AGPL-3.0 text for retained AGPL packages', () => {
    const license = readText('licenses/AGPL-3.0.txt')

    expect(license).toContain('GNU AFFERO GENERAL PUBLIC LICENSE')
    expect(license).toContain('Version 3, 19 November 2007')
    expect(license).toContain('END OF TERMS AND CONDITIONS')
  })

  it('records every retained AGPL package path without relabeling it', () => {
    const notices = readText('THIRD_PARTY_NOTICES.md')
    const inventory = readText('licenses/README.md')

    for (const relativePath of agplPackagePaths) {
      const manifest = JSON.parse(readText(relativePath)) as { license?: string }
      expect(manifest.license, relativePath).toBe('AGPL-3.0')
      expect(notices, relativePath).toContain(`\`${relativePath}\``)
    }

    expect(notices).toContain('licenses/AGPL-3.0.txt')
    expect(notices).toContain('must not relabel these packages as MIT or Apache-2.0')
    expect(inventory).toContain('AGPL-3.0.txt')
  })

  it('does not publish permissive-only Flatpak license metadata', () => {
    const metainfo = readText('flatpak/com.storyengine.desktop.metainfo.xml')

    expect(metainfo).toContain(
      '<project_license>Apache-2.0 AND AGPL-3.0-only</project_license>'
    )
    expect(metainfo).not.toContain(
      '<project_license>Apache-2.0</project_license>'
    )
  })

  it('bundles notices and license texts with every desktop configuration', () => {
    for (const relativePath of desktopTauriConfigs) {
      const config = JSON.parse(readText(relativePath)) as {
        bundle?: { resources?: Record<string, string> }
      }
      const resources = config.bundle?.resources ?? {}

      expect(resources, relativePath).toHaveProperty(
        '../THIRD_PARTY_NOTICES.md',
        'THIRD_PARTY_NOTICES.md'
      )
      expect(resources, relativePath).toHaveProperty('../licenses/', 'licenses/')
    }
  })
})
