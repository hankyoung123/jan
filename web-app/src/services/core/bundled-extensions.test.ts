import { describe, expect, it, vi } from 'vitest'

class BundledExtensionStub {}

vi.mock('@janhq/assistant-extension', () => ({ default: BundledExtensionStub }))
vi.mock('@janhq/conversational-extension', () => ({ default: BundledExtensionStub }))
vi.mock('@janhq/download-extension', () => ({ default: BundledExtensionStub }))
vi.mock('@janhq/llamacpp-extension', () => ({ default: BundledExtensionStub }))
vi.mock('@janhq/mlx-extension', () => ({ default: BundledExtensionStub }))

import { getBundledExtensions } from './bundled-extensions'

describe('closed product extension bundle', () => {
  it('excludes the legacy AGPL RAG and vector database implementations', async () => {
    const names = (await getBundledExtensions()).map(({ name }) => name)

    expect(names).not.toContain('@janhq/rag-extension')
    expect(names).not.toContain('@janhq/vector-db-extension')
  })
})
