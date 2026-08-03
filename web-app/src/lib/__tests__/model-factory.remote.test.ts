import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => {
  const responses = vi.fn(() => ({ kind: 'openai-responses' }))
  const languageModel = vi.fn(() => ({ kind: 'compatible' }))
  return {
    responses,
    languageModel,
    createOpenAI: vi.fn(() => ({ responses })),
    createOpenAICompatible: vi.fn(() => ({ languageModel })),
  }
})

vi.mock('@ai-sdk/openai', () => ({ createOpenAI: h.createOpenAI }))
vi.mock('@ai-sdk/openai-compatible', () => ({ createOpenAICompatible: h.createOpenAICompatible }))
vi.mock('@ai-sdk/anthropic', () => ({ createAnthropic: vi.fn() }))
vi.mock('@ai-sdk/google', () => ({ createGoogleGenerativeAI: vi.fn() }))
vi.mock('@ai-sdk/mistral', () => ({ createMistral: vi.fn() }))
vi.mock('@ai-sdk/xai', () => ({ createXai: vi.fn() }))
vi.mock('ai', () => ({
  extractReasoningMiddleware: vi.fn(() => ({})),
  wrapLanguageModel: vi.fn(({ model }) => model),
}))
vi.mock('@tauri-apps/plugin-http', () => ({ fetch: vi.fn() }))
vi.mock('@/lib/platform/utils', () => ({ isPlatformTauri: () => false }))

import { ModelFactory } from '../model-factory'

const provider = (overrides: Partial<ModelProvider> = {}): ModelProvider => ({
  provider: 'openai',
  active: true,
  base_url: 'https://api.example/v1',
  api_key: 'secret',
  models: [],
  ...overrides,
})

describe('ModelFactory cloud-only dispatch', () => {
  beforeEach(() => vi.clearAllMocks())

  it('uses the Responses API for OpenAI', async () => {
    const model = await ModelFactory.createModel('gpt-story', provider())
    expect(model).toEqual({ kind: 'openai-responses' })
    expect(h.createOpenAI).toHaveBeenCalledOnce()
    expect(h.responses).toHaveBeenCalledWith('gpt-story')
  })

  it('uses the OpenAI-compatible client for a custom cloud provider', async () => {
    const model = await ModelFactory.createModel(
      'novel-model',
      provider({ provider: 'custom-cloud' })
    )
    expect(model).toEqual({ kind: 'compatible' })
    expect(h.createOpenAICompatible).toHaveBeenCalledWith(
      expect.objectContaining({
        name: 'custom-cloud',
        baseURL: 'https://api.example/v1',
      })
    )
  })

  it('requires a configured cloud API key', async () => {
    await expect(
      ModelFactory.createModel(
        'novel-model',
        provider({ provider: 'custom-cloud', api_key: '' })
      )
    ).rejects.toThrow('No API key configured')
  })
})
