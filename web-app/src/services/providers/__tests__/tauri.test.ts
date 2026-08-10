import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({ fetch: vi.fn(), invoke: vi.fn() }))

vi.mock('@tauri-apps/plugin-http', () => ({ fetch: h.fetch }))
vi.mock('@tauri-apps/api/core', () => ({ invoke: h.invoke }))

import { TauriProvidersService } from '../tauri'

describe('TauriProvidersService remote-only boundary', () => {
  beforeEach(() => vi.clearAllMocks())

  it('returns only configured cloud provider definitions', async () => {
    const providers = await new TauriProvidersService().getProviders()
    expect(providers.some((item) => item.provider === 'openai')).toBe(true)
    const openCodeGo = providers.find(
      (item) => item.provider === 'opencode-go'
    )
    expect(openCodeGo?.base_url).toBe('https://opencode.ai/zen/go/v1')
    expect(openCodeGo?.models).toHaveLength(19)
    expect(
      providers.some((item) => ['llamacpp', 'llama.cpp', 'mlx'].includes(item.provider))
    ).toBe(false)
  })

  it('fetches model ids from an OpenAI-compatible remote endpoint', async () => {
    h.fetch.mockResolvedValue({
      ok: true,
      status: 200,
      statusText: 'OK',
      json: vi.fn().mockResolvedValue({ data: [{ id: 'story-large' }] }),
    })
    const models = await new TauriProvidersService().fetchModelsFromProvider({
      provider: 'custom-cloud',
      active: true,
      base_url: 'https://models.example/v1',
      api_key: 'secret',
      models: [],
    })
    expect(models).toEqual(['story-large'])
    expect(h.fetch).toHaveBeenCalledWith(
      'https://models.example/v1/models',
      expect.objectContaining({ method: 'GET' })
    )
  })

  it('deletes provider credentials through the native keyring boundary', async () => {
    h.invoke.mockResolvedValue(undefined)
    await new TauriProvidersService().deleteProviderKeys('openai')
    expect(h.invoke).toHaveBeenCalledWith('delete_provider_keys', {
      provider: 'openai',
    })
  })

  it('leaves settings persistence to the provider store and bridge', async () => {
    await expect(
      new TauriProvidersService().updateSettings('openai', [])
    ).resolves.toBeUndefined()
    expect(h.invoke).not.toHaveBeenCalled()
  })
})
