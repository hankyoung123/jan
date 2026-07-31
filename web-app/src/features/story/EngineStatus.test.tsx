import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  resolveEngineRuntime: vi.fn(),
  restartEngineRuntime: vi.fn(),
  subscribeEngineRuntime: vi.fn(),
}))

vi.mock('./engine', () => ({
  resolveEngineRuntime: h.resolveEngineRuntime,
  restartEngineRuntime: h.restartEngineRuntime,
  subscribeEngineRuntime: h.subscribeEngineRuntime,
}))

import { EngineStatus } from './EngineStatus'

describe('EngineStatus', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    h.subscribeEngineRuntime.mockResolvedValue(() => undefined)
  })

  it('shows the ready state reported by the managed Sidecar', async () => {
    h.resolveEngineRuntime.mockResolvedValue({
      phase: 'ready',
      base_url: 'http://127.0.0.1:41000',
      websocket_url: 'ws://127.0.0.1:41000/ws/events',
      session_token: 'runtime-secret',
      restart_count: 0,
      last_error: null,
    })

    render(<EngineStatus />)

    expect(await screen.findByRole('status')).toHaveTextContent('故事引擎已连接')
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('offers recovery after a crash and invokes the native restart command', async () => {
    h.resolveEngineRuntime.mockResolvedValue({
      phase: 'crashed',
      base_url: 'http://127.0.0.1:41000',
      websocket_url: null,
      session_token: null,
      restart_count: 0,
      last_error: 'Story Engine exited unexpectedly',
    })
    h.restartEngineRuntime.mockResolvedValue({
      phase: 'starting',
      base_url: 'http://127.0.0.1:42000',
      websocket_url: 'ws://127.0.0.1:42000/ws/events',
      session_token: 'new-runtime-secret',
      restart_count: 1,
      last_error: null,
    })

    render(<EngineStatus />)
    fireEvent.click(await screen.findByRole('button', { name: '重新启动' }))

    await waitFor(() => expect(h.restartEngineRuntime).toHaveBeenCalledOnce())
    expect(screen.getByRole('status')).toHaveTextContent('故事引擎启动中')
  })
})
