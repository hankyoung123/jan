import { CircleAlert, LoaderCircle, UserRound } from 'lucide-react'
import { useEffect, useState } from 'react'

import { engineRequest } from '../engine'
import { IntentInput } from './IntentInput'
import { SceneView } from './SceneView'
import { SelfLens } from './SelfLens'

const projectId = 'last-ferry-before'

type SessionResponse = {
  perception: {
    scene_text: string
    player_state_summary: string
    visible_changes: string[]
    checkpoint_id: string
    world_time: string
  }
  visible_events: string[]
  player_state: {
    identity: string
    capabilities: string[]
    conditions: string[]
    possessions: string[]
    relationships: string[]
  }
  checkpoint_id: string
  world_time: string
}

export function WorldSessionView() {
  const [session, setSession] = useState<SessionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [showSelf, setShowSelf] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let disposed = false
    void engineRequest<SessionResponse>(`/projects/${projectId}/simulation/session`)
      .then((response) => {
        if (!disposed) setSession(response)
      })
      .catch((cause: unknown) => {
        if (!disposed) setError(cause instanceof Error ? cause.message : '无法打开世界')
      })
      .finally(() => {
        if (!disposed) setLoading(false)
      })
    return () => {
      disposed = true
    }
  }, [])

  async function submit(text: string) {
    setSending(true)
    setError(null)
    try {
      const response = await engineRequest<SessionResponse>(
        `/projects/${projectId}/simulation/turn`,
        { method: 'POST', body: JSON.stringify({ text }) }
      )
      setSession(response)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '这次行动没有完成')
    } finally {
      setSending(false)
    }
  }

  if (loading) {
    return <main className="grid h-svh place-items-center bg-neutral-50 dark:bg-background"><LoaderCircle className="animate-spin text-muted-foreground" size={22} /></main>
  }

  if (!session) {
    return (
      <main className="grid h-svh place-items-center bg-neutral-50 px-6 dark:bg-background">
        <p className="flex items-center gap-2 text-sm text-destructive"><CircleAlert size={16} />{error ?? '无法打开世界'}</p>
      </main>
    )
  }

  const events = session.visible_events
  return (
    <main className="h-svh overflow-y-auto bg-neutral-50 px-5 pb-10 pt-10 dark:bg-background md:px-8">
      <div className="mx-auto max-w-5xl">
        <header className="flex items-start justify-between border-b pb-5">
          <div>
            <h1 className="font-studio text-2xl font-medium">末班船之前</h1>
            <p className="mt-1 text-sm text-muted-foreground">港口旅馆 · {session.world_time}</p>
          </div>
          <button
            aria-expanded={showSelf}
            aria-label="查看自身状态"
            className="grid size-9 place-items-center border bg-background hover:bg-accent lg:hidden"
            onClick={() => setShowSelf((visible) => !visible)}
            title="查看自身状态"
            type="button"
          >
            <UserRound size={17} />
          </button>
        </header>
        {error && <p className="mt-4 text-sm text-destructive" role="status">{error}</p>}
        <div className="mt-8 grid gap-10 lg:grid-cols-[minmax(0,1fr)_220px]">
          <div className="min-w-0">
            <SceneView sceneText={session.perception.scene_text} visibleEvents={events} />
            <div className="mt-10">
              <IntentInput disabled={sending} onSubmit={submit} />
            </div>
          </div>
          <div className={showSelf ? 'block' : 'hidden lg:block'}>
            <SelfLens player={session.player_state} />
          </div>
        </div>
      </div>
    </main>
  )
}
