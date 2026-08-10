import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => {
  const responses = vi.fn(() => ({ kind: 'openai-responses' }))
  const languageModel = vi.fn(() => ({ kind: 'compatible' }))
  const anthropicModel = vi.fn(() => ({ kind: 'anthropic' }))
  return {
    responses,
    languageModel,
    anthropicModel,
    createOpenAI: vi.fn(() => ({ responses })),
    createOpenAICompatible: vi.fn(() => ({ languageModel })),
    createAnthropic: vi.fn(() => anthropicModel),
  }
})

vi.mock('@ai-sdk/openai', () => ({ createOpenAI: h.createOpenAI }))
vi.mock('@ai-sdk/openai-compatible', () => ({ createOpenAICompatible: h.createOpenAICompatible }))
vi.mock('@ai-sdk/anthropic', () => ({ createAnthropic: h.createAnthropic }))
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

  it('dispatches OpenCode Go models through their documented wire APIs', async () => {
    const openCodeGo = provider({
      provider: 'opencode-go',
      base_url: 'https://opencode.ai/zen/go/v1',
      models: [
        { id: 'kimi-k3', api_type: 'openai' },
        { id: 'gpt-5.6-luna', api_type: 'openai-responses' },
        { id: 'minimax-m3', api_type: 'anthropic' },
      ],
    })

    await expect(
      ModelFactory.createModel('kimi-k3', openCodeGo)
    ).resolves.toEqual({ kind: 'compatible' })
    await expect(
      ModelFactory.createModel('gpt-5.6-luna', openCodeGo)
    ).resolves.toEqual({ kind: 'openai-responses' })
    await expect(
      ModelFactory.createModel('minimax-m3', openCodeGo)
    ).resolves.toEqual({ kind: 'anthropic' })

    expect(h.languageModel).toHaveBeenCalledWith('kimi-k3')
    expect(h.responses).toHaveBeenCalledWith('gpt-5.6-luna')
    expect(h.anthropicModel).toHaveBeenCalledWith('minimax-m3')
    expect(h.createAnthropic).toHaveBeenCalledWith(
      expect.objectContaining({
        baseURL: 'https://opencode.ai/zen/go/v1',
        headers: expect.objectContaining({
          'anthropic-version': '2023-06-01',
        }),
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
