import { describe, expect, it, vi } from 'vitest'

class BundledExtensionStub {}

vi.mock('@janhq/assistant-extension', () => ({ default: BundledExtensionStub }))
vi.mock('@janhq/conversational-extension', () => ({ default: BundledExtensionStub }))

import { getBundledExtensions } from './bundled-extensions'

describe('closed product extension bundle', () => {
  it('contains only the retained assistant and conversation extensions', async () => {
    const names = (await getBundledExtensions()).map(({ name }) => name)

    expect(names).toEqual([
      '@janhq/assistant-extension',
      '@janhq/conversational-extension',
    ])
  })
})
