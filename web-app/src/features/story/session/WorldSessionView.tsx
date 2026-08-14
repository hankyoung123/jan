import { Bot, CircleAlert, GitBranch, LoaderCircle, UserRound } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { EngineRequestError, engineRequest } from '../engine'
import { useSimulationStream } from '../evolution/useSimulationStream'
import { AgentConsole } from './AgentConsole'
import { IntentInput } from './IntentInput'
import { SceneView } from './SceneView'
import { SelfLens } from './SelfLens'
import { Timeline, type TimelineEntry } from './Timeline'

const projectId = 'rainy-night-apartment'

type SessionResponse = {
  session_id: string
  branch_id: string
  step: number
  status: string
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

type Branch = {
  branch_id: string
  head_checkpoint_id: string | null
  content_locale: string
}

type PendingIntent = {
  text: string
  commandId: string
}

function branchPath(path: string, branchId: string) {
  return branchId === 'main' ? path : `${path}?branch_id=${encodeURIComponent(branchId)}`
}

export function WorldSessionView() {
  const [session, setSession] = useState<SessionResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [showSelf, setShowSelf] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [branchId, setBranchId] = useState('main')
  const [branches, setBranches] = useState<Branch[]>([])
  const [timeline, setTimeline] = useState<TimelineEntry[]>([])
  const [retryIntent, setRetryIntent] = useState<PendingIntent | null>(null)
  const [agentConsoleCollapsed, setAgentConsoleCollapsed] = useState(false)
  const [mobileAgentConsoleOpen, setMobileAgentConsoleOpen] = useState(false)

  const refreshStreamSession = useCallback(() => {
    void engineRequest<SessionResponse>(
      branchPath(`/projects/${projectId}/simulation/session`, branchId)
    ).then(setSession).catch(() => undefined)
  }, [branchId])

  const { viewState } = useSimulationStream({
    projectId,
    sessionId: session?.session_id,
    sessionStatus: session?.status,
    currentStep: session?.step,
    branchId: session?.branch_id ?? branchId,
    onSessionChanged: refreshStreamSession,
  })

  useEffect(() => {
    let disposed = false
    setLoading(true)
    setError(null)
    setRetryIntent(null)
    void engineRequest<SessionResponse>(
      branchPath(`/projects/${projectId}/simulation/session`, branchId)
    )
      .then(async (response) => {
        const [availableBranches, checkpoints] = await Promise.all([
          engineRequest<Branch[]>(`/projects/${projectId}/branches`),
          engineRequest<TimelineEntry[]>(`/projects/${projectId}/branches/${encodeURIComponent(branchId)}/timeline`),
        ])
        return [response, availableBranches, checkpoints] as const
      })
      .then(([response, availableBranches, checkpoints]) => {
        if (disposed) return
        setSession(response)
        setBranches(availableBranches)
        setTimeline(checkpoints)
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
  }, [branchId])

  async function sendIntent(intent: PendingIntent) {
    setSending(true)
    setError(null)
    try {
      const response = await engineRequest<SessionResponse>(
        branchPath(`/projects/${projectId}/simulation/turn`, branchId),
        {
          method: 'POST',
          body: JSON.stringify({
            text: intent.text,
            command_id: intent.commandId,
          }),
        }
      )
      setRetryIntent(null)
      setSession(response)
      setTimeline((entries) => entries.map((entry) => ({
        ...entry,
        is_current: entry.checkpoint_id === response.checkpoint_id,
      })))
      void engineRequest<TimelineEntry[]>(`/projects/${projectId}/branches/${encodeURIComponent(branchId)}/timeline`)
        .then(setTimeline)
        .catch(() => undefined)
    } catch (cause) {
      if (cause instanceof EngineRequestError) {
        setRetryIntent(null)
        setError(cause.message)
      } else {
        try {
          const refreshed = await engineRequest<SessionResponse>(
            branchPath(`/projects/${projectId}/simulation/session`, branchId)
          )
          setSession(refreshed)
        } catch {
          // Preserve the original command even when the refresh also loses network.
        }
        setRetryIntent(intent)
        setError('连接中断，已刷新当前世界；可安全重试这次意图。')
      }
    } finally {
      setSending(false)
    }
  }

  function submit(text: string) {
    return sendIntent({
      text,
      commandId: `interactive:${crypto.randomUUID()}`,
    })
  }

  async function fork(entry: TimelineEntry) {
    setSending(true)
    setError(null)
    const nextBranchId = `fork-${Date.now().toString(36)}`
    try {
      await engineRequest<Branch>(`/projects/${projectId}/branches`, {
        method: 'POST',
        body: JSON.stringify({
          branch_id: nextBranchId,
          source_checkpoint_id: entry.checkpoint_id,
          parent_branch_id: branchId,
          content_locale: 'zh-CN',
        }),
      })
      setSending(false)
      setBranchId(nextBranchId)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '无法创建分支')
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
  const layoutClass = agentConsoleCollapsed
    ? 'mx-auto grid max-w-[90rem] gap-6 lg:grid-cols-[minmax(0,1fr)_48px]'
    : 'mx-auto grid max-w-[90rem] gap-6 lg:grid-cols-[minmax(0,1fr)_360px]'
  return (
    <main className="h-svh overflow-y-auto bg-neutral-50 px-5 pb-10 pt-10 dark:bg-background md:px-8">
      <div className={layoutClass}>
        <div className="min-w-0">
          <header className="flex items-start justify-between border-b pb-5">
          <div>
            <h1 className="font-studio text-2xl font-medium">雨夜公寓</h1>
            <p className="mt-1 text-sm text-muted-foreground">四楼楼道 · {session.world_time}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              aria-expanded={mobileAgentConsoleOpen}
              className="flex h-9 items-center gap-2 border bg-background px-2 text-xs hover:bg-accent lg:hidden"
              onClick={() => setMobileAgentConsoleOpen(true)}
              type="button"
            >
              <Bot size={15} />
              Agent Console
            </button>
            <label className="flex items-center gap-2 border bg-background px-2 text-xs text-muted-foreground">
              <GitBranch size={14} />
              <select
                aria-label="分支"
                className="h-8 max-w-32 bg-transparent text-foreground outline-none"
                onChange={(event) => setBranchId(event.target.value)}
                value={branchId}
              >
                {branches.map((branch) => <option key={branch.branch_id} value={branch.branch_id}>{branch.branch_id}</option>)}
              </select>
            </label>
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
          </div>
          </header>
          {error && (
          <div className="mt-4 flex items-center gap-3 text-sm text-destructive" role="status">
            <p>{error}</p>
            {retryIntent && (
              <button
                className="border border-destructive/40 px-2 py-1 text-xs hover:bg-destructive/5 disabled:opacity-50"
                disabled={sending}
                onClick={() => void sendIntent(retryIntent)}
                type="button"
              >
                重试这次意图
              </button>
            )}
          </div>
          )}
          <Timeline entries={timeline} onFork={fork} pending={sending} />
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
        <div className="sticky top-0 hidden h-[calc(100svh-5rem)] lg:block">
          <AgentConsole
            collapsed={agentConsoleCollapsed}
            onCollapsedChange={setAgentConsoleCollapsed}
            viewState={viewState}
          />
        </div>
      </div>
      {mobileAgentConsoleOpen && (
        <div
          aria-label="Agent Console"
          aria-modal="true"
          className="fixed inset-0 z-50 overflow-y-auto bg-background p-3 lg:hidden"
          role="dialog"
        >
          <div className="h-[calc(100svh-1.5rem)] min-h-[36rem]">
            <AgentConsole
              collapsed={false}
              onCollapsedChange={() => setMobileAgentConsoleOpen(false)}
              viewState={viewState}
            />
          </div>
        </div>
      )}
    </main>
  )
}
