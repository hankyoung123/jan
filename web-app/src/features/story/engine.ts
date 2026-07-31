import { invoke, isTauri } from '@tauri-apps/api/core'

export type EnginePhase = 'starting' | 'ready' | 'stopped' | 'crashed'

interface EngineRuntimeState {
  phase: EnginePhase
  base_url: string | null
  websocket_url: string | null
  session_token: string | null
  restart_count: number
  last_error: string | null
}

const developmentRuntime: EngineRuntimeState = {
  phase: 'ready',
  base_url:
    import.meta.env.VITE_STORY_ENGINE_URL ?? 'http://127.0.0.1:39281',
  websocket_url: null,
  session_token:
    import.meta.env.VITE_STORY_ENGINE_TOKEN ?? 'development-token',
  restart_count: 0,
  last_error: null,
}

export async function resolveEngineRuntime(): Promise<EngineRuntimeState> {
  if (!isTauri()) return developmentRuntime
  return invoke<EngineRuntimeState>('engine_runtime_state')
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
