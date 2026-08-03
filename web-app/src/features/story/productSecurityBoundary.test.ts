import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const workspaceRoot = existsSync(resolve(process.cwd(), 'web-app'))
  ? process.cwd()
  : resolve(process.cwd(), '..')

const updaterSources = [
  'src-tauri/src/core/updater/hmac_client.rs',
  'src-tauri/src/core/updater/custom_updater.rs',
]

describe('Story Engine updater security boundary', () => {
  it('does not retain Jan signing variables or a hard-coded development secret', () => {
    for (const relativePath of updaterSources) {
      const source = readFileSync(join(workspaceRoot, relativePath), 'utf8')

      expect(source, relativePath).not.toContain('JAN_SIGNING_KEY')
      expect(source, relativePath).not.toContain('local-dev-test-key-not-for-production')
    }
  })

  it('uses an optional Story Engine signing key and explicit missing-key handling', () => {
    const hmacClient = readFileSync(
      join(workspaceRoot, 'src-tauri/src/core/updater/hmac_client.rs'),
      'utf8'
    )
    const customUpdater = readFileSync(
      join(workspaceRoot, 'src-tauri/src/core/updater/custom_updater.rs'),
      'utf8'
    )

    expect(hmacClient).toContain('STORY_ENGINE_UPDATE_SIGNING_KEY')
    expect(hmacClient).toContain('configured_signing_key')
    expect(customUpdater).toContain('SigningKeyUnavailable')
  })
})
