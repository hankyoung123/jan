import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  invoke: vi.fn(),
  isTauri: vi.fn(),
  listen: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({
  invoke: h.invoke,
  isTauri: h.isTauri,
}))
vi.mock('@tauri-apps/api/event', () => ({ listen: h.listen }))

import {
  engineRequest,
  resolveEngineRuntime,
  restartEngineRuntime,
  subscribeEngineRuntime,
} from './engine'

describe('Story Engine client', () => {
  beforeEach(() => {
    h.invoke.mockReset()
    h.isTauri.mockReset()
    h.listen.mockReset()
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

  it('restarts the managed native Sidecar through its dedicated command', async () => {
    h.isTauri.mockReturnValue(true)
    h.invoke.mockResolvedValue({ phase: 'starting', restart_count: 1 })

    await restartEngineRuntime()

    expect(h.invoke).toHaveBeenCalledWith('restart_story_engine')
  })

  it('forwards token-free native status events to the recovery UI', async () => {
    h.isTauri.mockReturnValue(true)
    const listener = vi.fn()
    const unlisten = vi.fn()
    h.listen.mockImplementation(async (_eventName, callback) => {
      callback({
        payload: {
          phase: 'crashed',
          base_url: 'http://127.0.0.1:41000',
          websocket_url: null,
          restart_count: 0,
          last_error: 'unexpected exit',
        },
      })
      return unlisten
    })

    await expect(subscribeEngineRuntime(listener)).resolves.toBe(unlisten)
    expect(h.listen).toHaveBeenCalledWith(
      'story-engine://status',
      expect.any(Function)
    )
    expect(listener).toHaveBeenCalledWith(
      expect.not.objectContaining({ session_token: expect.anything() })
    )
  })
})
