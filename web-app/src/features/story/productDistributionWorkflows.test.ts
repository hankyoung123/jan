import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const workspaceRoot = existsSync(resolve(process.cwd(), 'web-app'))
  ? process.cwd()
  : resolve(process.cwd(), '..')
const workflowRoot = join(workspaceRoot, '.github/workflows')

const retiredEntrypoints = [
  'autoqa-template.yml',
  'autoqa-manual-trigger.yml',
  'autoqa-migration.yml',
  'autoqa-reliability.yml',
  'jan-tauri-build-flatpak.yaml',
  'jan-tauri-build-nightly-external.yaml',
  'jan-tauri-build-nightly.yaml',
  'jan-tauri-build.yaml',
  'manual-build-portable.yml',
  'publish-npm-core.yml',
]

const archivedJanPackagingTemplates = [
  'template-get-update-version.yml',
  'template-tauri-build-linux-x64-external.yml',
  'template-tauri-build-linux-x64-flatpak.yml',
  'template-tauri-build-linux-x64.yml',
  'template-tauri-build-macos-external.yml',
  'template-tauri-build-macos.yml',
  'template-tauri-build-windows-x64-external.yml',
  'template-tauri-build-windows-x64.yml',
  'tauri.bundle.windows.nsis.template',
]

describe('Story Engine distribution workflow boundary', () => {
  it('retires inherited Jan product workflow entrypoints', () => {
    for (const workflow of retiredEntrypoints) {
      expect(existsSync(join(workflowRoot, workflow)), workflow).toBe(false)
    }
  })

  it('uses a product-owned Linux/Flatpak packaging entrypoint', () => {
    const workflowPath = join(
      workflowRoot,
      'story-engine-build-linux-flatpak.yml'
    )
    const manifestPath = join(
      workspaceRoot,
      'flatpak/com.storyengine.desktop.yml'
    )
    const workflow = readFileSync(workflowPath, 'utf8')
    const manifest = readFileSync(manifestPath, 'utf8')

    expect(existsSync(workflowPath)).toBe(true)
    expect(workflow).toContain('Story Engine Linux Flatpak')
    expect(workflow).toContain('workflow_dispatch:')
    expect(workflow).toContain('workflow_call:')
    expect(workflow).toContain('flatpak-builder')
    expect(workflow).toContain('com.storyengine.desktop')
    expect(workflow).toContain(
      'flatpak --user install --noninteractive --assumeyes --bundle'
    )
    expect(workflow).toContain('flatpak info --user com.storyengine.desktop')
    expect(workflow).toContain(
      'flatpak --user uninstall --noninteractive --assumeyes com.storyengine.desktop'
    )
    expect(workflow).toContain(
      'stage/usr/lib/story-engine-desktop/resources/story-engine/story-engine'
    )
    expect(workflow).toContain(
      'stage/usr/lib/story-engine-desktop/THIRD_PARTY_NOTICES.md'
    )
    expect(workflow).toContain(
      'stage/usr/lib/story-engine-desktop/licenses/AGPL-3.0.txt'
    )
    expect(workflow).not.toContain('janhq/jan')
    expect(workflow).not.toContain('delta.jan.ai')

    expect(manifest).toContain('app-id: com.storyengine.desktop')
    expect(manifest).toContain('story-engine-desktop')
  })

  it('owns macOS and Windows build/smoke entrypoints', () => {
    const macWorkflow = readFileSync(
      join(workflowRoot, 'story-engine-build-macos.yml'),
      'utf8'
    )
    const windowsWorkflow = readFileSync(
      join(workflowRoot, 'story-engine-build-windows.yml'),
      'utf8'
    )

    for (const [name, workflow] of [
      ['macOS', macWorkflow],
      ['Windows', windowsWorkflow],
    ] as const) {
      expect(workflow, name).toContain('Story Engine')
      expect(workflow, name).toContain('workflow_dispatch:')
      expect(workflow, name).toContain('workflow_call:')
      expect(workflow, name).not.toContain('delta.jan.ai')
      expect(workflow, name).not.toContain('janhq/jan')
    }

    expect(macWorkflow).toContain('hdiutil attach')
    expect(macWorkflow).toContain('com.storyengine.desktop')
    expect(macWorkflow).toContain('Contents/Resources/THIRD_PARTY_NOTICES.md')
    expect(macWorkflow).toContain('Contents/Resources/licenses/AGPL-3.0.txt')
    expect(macWorkflow).toMatch(/^\s*run: yarn build\s*$/m)
    expect(windowsWorkflow).toContain("Start-Process -FilePath $installer.FullName")
    expect(windowsWorkflow).toMatch(/^\s*run: yarn build\s*$/m)
    expect(windowsWorkflow).toContain('uninstall.exe')
    expect(windowsWorkflow).toContain('THIRD_PARTY_NOTICES.md')
    expect(windowsWorkflow).toContain('licenses/AGPL-3.0.txt')
  })

  it('keeps inherited Jan packaging templates in the upstream archive', () => {
    const archiveReadme = join(
      workspaceRoot,
      'docs/upstream/jan/workflows-packaging/README.md'
    )
    expect(existsSync(archiveReadme)).toBe(true)
    expect(readFileSync(archiveReadme, 'utf8')).toMatch(
      /are not active\s+Story Engine workflow entrypoints\./
    )

    for (const workflow of archivedJanPackagingTemplates) {
      const path = join(
        workspaceRoot,
        'docs/upstream/jan/workflows-packaging',
        workflow
      )
      expect(existsSync(path), workflow).toBe(true)
      expect(existsSync(join(workflowRoot, workflow)), workflow).toBe(false)
    }
  })

  it('keeps active workflows disconnected from Jan distribution services', () => {
    const forbidden = [
      'https://github.com/janhq/jan/releases',
      'https://delta.jan.ai',
      'https://catalog.jan.ai',
      'uses: janhq/jan/.github/workflows',
      'softprops/action-gh-release',
      'yarn publish --access public',
    ]

    for (const workflow of readdirSync(workflowRoot)) {
      if (!/\.ya?ml$/.test(workflow)) continue
      const contents = readFileSync(join(workflowRoot, workflow), 'utf8')
      if (/^  workflow_call:/m.test(contents)) continue

      for (const marker of forbidden) {
        expect(contents, `${workflow}: ${marker}`).not.toContain(marker)
      }
    }
  })

  it('keeps the former Jan AutoQA runner outside the product test surface', () => {
    const qaReadme = readFileSync(join(workspaceRoot, 'autoqa/README.md'), 'utf8')
    const makefile = readFileSync(join(workspaceRoot, 'Makefile'), 'utf8')
    expect(qaReadme).toContain('story-engine-ci.yml')
    expect(qaReadme).toContain('docs/upstream/jan/autoqa/')
    expect(existsSync(join(workspaceRoot, 'autoqa/main.py'))).toBe(false)
    expect(
      existsSync(join(workspaceRoot, '.github/workflows/autoqa-template.yml'))
    ).toBe(false)
    expect(makefile).toContain('scripts/setup-android-env.sh')
    expect(makefile).not.toContain('autoqa/scripts/')
    expect(makefile).not.toContain('~/jan')
  })
})
