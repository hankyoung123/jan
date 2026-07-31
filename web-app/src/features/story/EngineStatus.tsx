import { CheckCircle2, LoaderCircle, RotateCcw, TriangleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'

import {
  resolveEngineRuntime,
  restartEngineRuntime,
  subscribeEngineRuntime,
  type EngineRuntimeStatus,
} from './engine'

const startingState: EngineRuntimeStatus = {
  phase: 'starting',
  base_url: null,
  websocket_url: null,
  restart_count: 0,
  last_error: null,
}

export function EngineStatus() {
  const [runtime, setRuntime] = useState<EngineRuntimeStatus>(startingState)

  useEffect(() => {
    let disposed = false
    let unlisten: () => void = () => undefined

    void resolveEngineRuntime()
      .then((state) => {
        if (!disposed) setRuntime(state)
      })
      .catch((error: unknown) => {
        if (!disposed) {
          setRuntime({
            ...startingState,
            phase: 'crashed',
            last_error:
              error instanceof Error ? error.message : '无法连接故事引擎',
          })
        }
      })

    void subscribeEngineRuntime((state) => {
      if (!disposed) setRuntime(state)
    }).then((cleanup) => {
      if (disposed) cleanup()
      else unlisten = cleanup
    })

    return () => {
      disposed = true
      unlisten()
    }
  }, [])

  async function restart() {
    setRuntime((current) => ({
      ...current,
      phase: 'starting',
      last_error: null,
    }))
    try {
      setRuntime(await restartEngineRuntime())
    } catch (error) {
      setRuntime((current) => ({
        ...current,
        phase: 'crashed',
        last_error:
          error instanceof Error ? error.message : '故事引擎重新启动失败',
      }))
    }
  }

  const unavailable = runtime.phase === 'crashed' || runtime.phase === 'stopped'
  const label =
    runtime.phase === 'ready'
      ? '故事引擎已连接'
      : runtime.phase === 'starting'
        ? '故事引擎启动中'
        : runtime.phase === 'crashed'
          ? '故事引擎已崩溃'
          : '故事引擎已停止'

  return (
    <div
      aria-live="polite"
      className={`fixed bottom-4 right-4 z-40 flex min-h-9 items-center gap-2 rounded-md border bg-background/95 px-3 text-xs shadow-sm backdrop-blur ${unavailable ? 'border-destructive/40 text-destructive' : 'text-muted-foreground'}`}
      role="status"
      title={runtime.last_error ?? undefined}
    >
      {runtime.phase === 'ready' ? (
        <CheckCircle2 className="text-emerald-600" size={14} />
      ) : runtime.phase === 'starting' ? (
        <LoaderCircle className="animate-spin" size={14} />
      ) : (
        <TriangleAlert size={14} />
      )}
      <span>{label}</span>
      {unavailable && (
        <button
          className="ml-1 inline-flex items-center gap-1 rounded border px-2 py-1 font-medium hover:bg-accent"
          onClick={() => void restart()}
          type="button"
        >
          <RotateCcw size={12} />
          重新启动
        </button>
      )}
    </div>
  )
}
