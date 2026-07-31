import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  invoke: vi.fn(),
  isTauri: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: h.invoke,
  isTauri: h.isTauri,
}))

import { engineRequest, resolveEngineRuntime } from './engine'

describe('Story Engine client', () => {
  beforeEach(() => {
    h.invoke.mockReset()
    h.isTauri.mockReset()
    vi.restoreAllMocks()
  })

  it('uses the development loopback runtime outside Tauri', async () => {
    h.isTauri.mockReturnValue(false)

    await expect(resolveEngineRuntime()).resolves.toMatchObject({
      phase: 'ready',
      base_url: 'http://127.0.0.1:39281',
      session_token: 'development-token',
    })
  })

  it('authorizes native requests with the process-local runtime token', async () => {
    h.isTauri.mockReturnValue(true)
    h.invoke.mockResolvedValue({
      phase: 'ready',
      base_url: 'http://127.0.0.1:41000',
      websocket_url: 'ws://127.0.0.1:41000/ws/events',
      session_token: 'runtime-secret',
      restart_count: 0,
      last_error: null,
    })
    const fetchMock = vi
      .spyOn(globalThis, 'fetch')
      .mockResolvedValue(new Response(JSON.stringify({ status: 'ok' })))

    await expect(engineRequest<{ status: string }>('/health')).resolves.toEqual({
      status: 'ok',
    })
    expect(h.invoke).toHaveBeenCalledWith('engine_runtime_state')
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:41000/health',
      expect.objectContaining({
        headers: expect.objectContaining({
          Authorization: 'Bearer runtime-secret',
        }),
      })
    )
  })

  it('surfaces structured engine errors without exposing the token', async () => {
    h.isTauri.mockReturnValue(true)
    h.invoke.mockResolvedValue({
      phase: 'ready',
      base_url: 'http://127.0.0.1:41000',
      websocket_url: null,
      session_token: 'runtime-secret',
      restart_count: 0,
      last_error: null,
    })
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({ detail: { message: '版本冲突' } }), {
        status: 409,
      })
    )

    await expect(engineRequest('/projects/fog-harbor/turns/1/confirm')).rejects.toThrow(
      '版本冲突'
    )
  })
})
