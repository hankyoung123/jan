import {
  CheckCircle2,
  LoaderCircle,
  Play,
  RotateCcw,
  Square,
  TriangleAlert,
} from 'lucide-react'
import { useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'

import {
  resolveEngineRuntime,
  restartEngineRuntime,
  startEngineRuntime,
  stopEngineRuntime,
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
  const [working, setWorking] = useState(false)

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

  async function transition(
    action: () => Promise<EngineRuntimeStatus>,
    failureMessage: string,
    starting = false
  ) {
    setWorking(true)
    if (starting) {
      setRuntime((current) => ({
        ...current,
        phase: 'starting',
        last_error: null,
      }))
    }
    try {
      setRuntime(await action())
    } catch (error) {
      setRuntime((current) => ({
        ...current,
        phase: 'crashed',
        last_error: error instanceof Error ? error.message : failureMessage,
      }))
    } finally {
      setWorking(false)
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
      {runtime.phase === 'ready' && (
        <>
          <Button
            aria-label="停止故事引擎"
            disabled={working}
            onClick={() =>
              void transition(
                stopEngineRuntime,
                '故事引擎停止失败'
              )
            }
            size="icon-xs"
            title="停止故事引擎"
            variant="ghost"
          >
            <Square />
          </Button>
          <Button
            aria-label="重新启动故事引擎"
            disabled={working}
            onClick={() =>
              void transition(
                restartEngineRuntime,
                '故事引擎重新启动失败',
                true
              )
            }
            size="icon-xs"
            title="重新启动故事引擎"
            variant="ghost"
          >
            <RotateCcw />
          </Button>
        </>
      )}
      {runtime.phase === 'stopped' && (
        <Button
          disabled={working}
          onClick={() =>
            void transition(startEngineRuntime, '故事引擎启动失败', true)
          }
          size="xs"
          variant="outline"
        >
          <Play />
          启动
        </Button>
      )}
      {runtime.phase === 'crashed' && (
        <Button
          disabled={working}
          onClick={() =>
            void transition(
              restartEngineRuntime,
              '故事引擎重新启动失败',
              true
            )
          }
          size="xs"
          variant="outline"
        >
          <RotateCcw />
          重新启动
        </Button>
      )}
    </div>
  )
}
