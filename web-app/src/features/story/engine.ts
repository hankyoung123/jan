import { invoke, isTauri } from '@tauri-apps/api/core'
import { listen, type UnlistenFn } from '@tauri-apps/api/event'
import {
  isEngineEvent,
  type EngineEventEnvelope,
} from '@story-engine/contracts'

export type EnginePhase = 'starting' | 'ready' | 'stopped' | 'crashed'

export interface EngineRuntimeState {
  phase: EnginePhase
  base_url: string | null
  websocket_url: string | null
  session_token: string | null
  restart_count: number
  last_error: string | null
}

export type EngineRuntimeStatus = Omit<EngineRuntimeState, 'session_token'>

const developmentBaseUrl =
  import.meta.env.VITE_STORY_ENGINE_URL ?? 'http://127.0.0.1:39281'

const developmentRuntime: EngineRuntimeState = {
  phase: 'ready',
  base_url: developmentBaseUrl,
  websocket_url: `${developmentBaseUrl.replace(/^http/, 'ws').replace(/\/$/, '')}/ws/events`,
  session_token:
    import.meta.env.VITE_STORY_ENGINE_TOKEN ?? 'development-token',
  restart_count: 0,
  last_error: null,
}

export async function resolveEngineRuntime(): Promise<EngineRuntimeState> {
  if (!isTauri()) return developmentRuntime
  return invoke<EngineRuntimeState>('engine_runtime_state')
}

export async function restartEngineRuntime(): Promise<EngineRuntimeState> {
  if (!isTauri()) return developmentRuntime
  return invoke<EngineRuntimeState>('restart_story_engine')
}

export async function startEngineRuntime(): Promise<EngineRuntimeState> {
  if (!isTauri()) return developmentRuntime
  return invoke<EngineRuntimeState>('start_story_engine')
}

export async function stopEngineRuntime(): Promise<EngineRuntimeState> {
  if (!isTauri()) {
    return {
      ...developmentRuntime,
      phase: 'stopped',
      base_url: null,
      websocket_url: null,
      session_token: null,
    }
  }
  return invoke<EngineRuntimeState>('stop_story_engine')
}

export async function subscribeEngineRuntime(
  listener: (state: EngineRuntimeStatus) => void
): Promise<UnlistenFn> {
  if (!isTauri()) return () => undefined
  return listen<EngineRuntimeStatus>('story-engine://status', (event) => {
    listener(event.payload)
  })
}

export async function subscribeProjectEvents(
  projectId: string,
  listener: (event: EngineEventEnvelope) => void
): Promise<() => void> {
  if (!/^[a-z0-9][a-z0-9-]*$/.test(projectId)) {
    throw new Error('invalid Story Engine project ID')
  }
  let disposed = false
  let socket: WebSocket | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let reconnectAttempt = 0
  let lastSequence = 0

  function scheduleReconnect() {
    if (disposed || reconnectTimer !== null) return
    const delay = Math.min(250 * 2 ** reconnectAttempt, 5_000)
    reconnectAttempt += 1
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      void connect().catch(() => scheduleReconnect())
    }, delay)
  }

  async function connect() {
    const runtime = await resolveEngineRuntime()
    if (!runtime.websocket_url || !runtime.session_token) {
      throw new Error(
        runtime.last_error ?? 'Story Engine event stream is unavailable'
      )
    }
    const url = new URL(runtime.websocket_url)
    url.searchParams.set('project_id', projectId)
    if (lastSequence > 0) {
      url.searchParams.set('after_sequence', String(lastSequence))
    }
    const nextSocket = new WebSocket(url.toString(), [
      'story-engine.v1',
      `story-engine.token.${runtime.session_token}`,
    ])
    socket = nextSocket
    nextSocket.onopen = () => {
      reconnectAttempt = 0
    }
    nextSocket.onmessage = (message) => {
      try {
        const value: unknown = JSON.parse(String(message.data))
        if (
          !isEngineEvent(value) ||
          value.project_id !== projectId ||
          value.sequence <= lastSequence
        ) {
          return
        }
        if (lastSequence > 0 && value.sequence > lastSequence + 1) {
          listener({
            ...value,
            type: 'stream.resync_required',
            payload: {
              reason: 'client_sequence_gap',
              after_sequence: lastSequence,
              latest_sequence: value.sequence,
            },
          })
        }
        lastSequence = value.sequence
        listener(value)
      } catch {
        // HTTP remains canonical when an event is malformed or incompatible.
      }
    }
    nextSocket.onclose = () => {
      if (socket === nextSocket) socket = null
      scheduleReconnect()
    }
  }

  await connect()
  return () => {
    disposed = true
    if (reconnectTimer !== null) clearTimeout(reconnectTimer)
    socket?.close()
    socket = null
  }
}

function errorMessage(payload: unknown, status: number): string {
  if (
    typeof payload === 'object' &&
    payload !== null &&
    'detail' in payload
  ) {
    const detail = payload.detail
    if (typeof detail === 'string') return detail
    if (
      typeof detail === 'object' &&
      detail !== null &&
      'message' in detail &&
      typeof detail.message === 'string'
    ) {
      return detail.message
    }
  }
  return `Story Engine returned ${status}`
}

export async function engineRequest<Response>(
  path: string,
  init: RequestInit = {}
): Promise<Response> {
  const runtime = await resolveEngineRuntime()
  if (!runtime.base_url || !runtime.session_token) {
    throw new Error(runtime.last_error ?? 'Story Engine is not running')
  }
  const response = await fetch(`${runtime.base_url}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${runtime.session_token}`,
      'Content-Type': 'application/json',
      ...init.headers,
    },
  })
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    throw new Error(errorMessage(payload, response.status))
  }
  return (await response.json()) as Response
}
