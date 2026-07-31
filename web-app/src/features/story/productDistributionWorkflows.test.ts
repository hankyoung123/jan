import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const workspaceRoot = existsSync(resolve(process.cwd(), 'web-app'))
  ? process.cwd()
  : resolve(process.cwd(), '..')
const workflowRoot = join(workspaceRoot, '.github/workflows')

const retiredEntrypoints = [
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

const retainedPackagingTemplates = [
  'template-get-update-version.yml',
  'template-tauri-build-linux-x64-external.yml',
  'template-tauri-build-linux-x64-flatpak.yml',
  'template-tauri-build-linux-x64.yml',
  'template-tauri-build-macos-external.yml',
  'template-tauri-build-macos.yml',
  'template-tauri-build-windows-x64-external.yml',
  'template-tauri-build-windows-x64.yml',
]

describe('Story Engine distribution workflow boundary', () => {
  it('retires inherited Jan product workflow entrypoints', () => {
    for (const workflow of retiredEntrypoints) {
      expect(existsSync(join(workflowRoot, workflow)), workflow).toBe(false)
    }
  })

  it('keeps desktop packaging implementations as inert reusable templates', () => {
    for (const workflow of retainedPackagingTemplates) {
      const path = join(workflowRoot, workflow)
      expect(existsSync(path), workflow).toBe(true)
      const contents = readFileSync(path, 'utf8')
      expect(contents, workflow).toContain('workflow_call:')
      expect(contents, workflow).not.toMatch(
        /^  (push|pull_request|schedule|workflow_dispatch):/m
      )
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
})
