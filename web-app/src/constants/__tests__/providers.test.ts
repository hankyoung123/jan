import { describe, expect, it } from 'vitest'
import { predefinedProviders } from '../providers'

describe('predefinedProviders', () => {
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
