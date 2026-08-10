import { describe, expect, it } from 'vitest'
import { predefinedProviders } from '../providers'

describe('predefinedProviders', () => {
  it('includes OpenCode Go with the official mixed-protocol model catalog', () => {
    const openCodeGo = predefinedProviders.find(
      (provider) => provider.provider === 'opencode-go'
    )

    expect(openCodeGo).toBeDefined()
    expect(openCodeGo?.active).toBe(true)
    expect(openCodeGo?.base_url).toBe('https://opencode.ai/zen/go/v1')
    expect(openCodeGo?.api_type).toBe('openai')
    expect(openCodeGo?.explore_models_url).toBe(
      'https://opencode.ai/docs/go/'
    )

    const modelsFor = (apiType: ProviderApiType) =>
      openCodeGo?.models
        .filter((model) => model.api_type === apiType)
        .map((model) => model.id)
    expect(modelsFor('openai')).toEqual([
      'grok-4.5',
      'glm-5.2',
      'glm-5.1',
      'kimi-k3',
      'kimi-k2.7-code',
      'kimi-k2.6',
      'deepseek-v4-pro',
      'deepseek-v4-flash',
      'mimo-v2.5',
      'mimo-v2.5-pro',
      'hy3',
    ])
    expect(modelsFor('openai-responses')).toEqual([
      'gpt-5.6-luna',
    ])
    expect(modelsFor('anthropic')).toEqual([
      'minimax-m3',
      'minimax-m2.7',
      'minimax-m2.5',
      'qwen3.8-max',
      'qwen3.7-max',
      'qwen3.7-plus',
      'qwen3.6-plus',
    ])
    expect(openCodeGo?.models).toHaveLength(19)
    expect(
      openCodeGo?.models.every((model) =>
        model.capabilities?.includes('tools')
      )
    ).toBe(true)
  })

  it('includes DeepSeek with official connection parameters', () => {
    const deepseek = predefinedProviders.find(
      (provider) => provider.provider === 'deepseek'
    )

    expect(deepseek).toBeDefined()
    expect(deepseek?.active).toBe(true)
    expect(deepseek?.base_url).toBe('https://api.deepseek.com')
    expect(deepseek?.explore_models_url).toContain('api-docs.deepseek.com')

    const apiKeySetting = deepseek?.settings.find(
      (setting) => setting.key === 'api-key'
    )
    expect(apiKeySetting?.title).toBe('API Key')
    expect(
      (
        apiKeySetting?.controller_props as unknown as {
          placeholder: string
          type: string
        }
      )?.type
    ).toBe('password')

    expect(deepseek?.models.map((model) => model.id)).toEqual([
      'deepseek-v4-pro',
      'deepseek-v4-flash',
    ])
    const pro = deepseek?.models.find((model) => model.id === 'deepseek-v4-pro')
    expect(pro?.capabilities).toEqual(['completion', 'tools'])
  })
})
