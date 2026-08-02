import type { components } from '@story-engine/contracts'
import { GitBranch, ListTree } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Input } from '@/components/ui/input'
import { useActiveStoryProjectId } from '../activeProject'
import { PageHeader, StatusPill, StoryPage } from '../components/StoryLayout'
import { engineRequest } from '../engine'
import { useBranchContext } from '../useBranchContext'

type SimulationLogRecord = components['schemas']['SimulationLogRecord']

export function SimulationHistoryView() {
  const projectId = useActiveStoryProjectId() ?? undefined
  const { branchId, setBranchId } = useBranchContext()
  const [records, setRecords] = useState<SimulationLogRecord[]>([])
  const [selectedStep, setSelectedStep] = useState<number | null>(null)
  const [loading, setLoading] = useState(Boolean(projectId))
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    setError(null)
    try {
      setRecords(
        await engineRequest<SimulationLogRecord[]>(
          `/projects/${projectId}/branches/${branchId}/simulation-trace?after_step=-1`
        )
      )
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '模拟历史加载失败')
    } finally {
      setLoading(false)
    }
  }, [branchId, projectId])

  useEffect(() => {
    void load()
  }, [load])

  const resolved = useMemo(
    () => records.filter((record) => record.result.resolved_turn !== null),
    [records]
  )
  const selected =
    resolved.find((record) => record.result.step === selectedStep) ??
    resolved.at(-1) ??
    null

  return (
    <StoryPage>
      <PageHeader
        eyebrow={projectId ? `${branchId} · ${resolved.length} 个已结算 Step` : '尚未选择项目'}
        title="模拟时间线"
        action={projectId ? <div className="flex items-center gap-2"><GitBranch size={15} /><Input aria-label="时间线分支" className="h-9 w-32 font-mono text-xs" defaultValue={branchId} onBlur={(event) => setBranchId(event.target.value)} /></div> : undefined}
      />
      {loading ? <p className="text-sm text-muted-foreground">正在读取模拟日志…</p> : error ? <p className="border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive" role="alert">{error}</p> : (
        <div className="grid min-h-[560px] border bg-background lg:grid-cols-[1fr_320px]">
          <main className="p-6">
            {resolved.map((record, index) => {
              const turn = record.result.resolved_turn!
              return <button className="relative grid w-full grid-cols-[46px_1fr] gap-4 pb-7 text-left" key={record.trace.trace_id} onClick={() => setSelectedStep(record.result.step)} type="button">{index < resolved.length - 1 && <span className="absolute bottom-0 left-5 top-10 w-px bg-border" />}<span className={`z-10 grid size-10 place-items-center border font-studio text-xs ${selected?.result.step === record.result.step ? 'bg-foreground text-background' : 'bg-background'}`}>{record.result.step}</span><span className="border-b pb-6"><span className="flex flex-wrap items-center gap-2"><StatusPill tone={record.result.boundary === 'none' ? 'neutral' : 'success'}>{record.result.boundary}</StatusPill><span className="font-mono text-[10px] text-muted-foreground">{record.checkpoint_id || 'log only'}</span></span><strong className="mt-2 block text-sm font-medium">{turn.raw_resolution_text}</strong><span className="mt-2 block text-xs text-muted-foreground">{turn.events.flatMap((event) => event.participant_ids).join(' · ') || 'GM world event'}</span></span></button>
            })}
            {resolved.length === 0 && <div className="grid min-h-72 place-items-center text-center text-sm text-muted-foreground"><div><ListTree className="mx-auto mb-3" />当前分支还没有模拟历史。</div></div>}
          </main>
          <aside className="border-t bg-muted/15 p-5 lg:border-l lg:border-t-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">Step Inspector</p>
            {selected ? <div className="mt-4 space-y-5 text-xs"><section><p className="text-muted-foreground">Session / Step</p><p className="mt-1 break-all font-mono">{selected.result.session_id}</p><p className="mt-1 font-mono">Step {selected.result.step}</p></section><section><p className="text-muted-foreground">Resolved Events</p>{selected.result.resolved_turn?.events.map((event) => <article className="mt-3 border-l-2 border-amber-500 pl-3" key={event.event_id}><p className="leading-5">{event.event_text}</p><p className="mt-2 break-all font-mono text-[10px] text-muted-foreground">{event.event_id} · {event.visibility}</p></article>)}</section><section><p className="text-muted-foreground">Trace</p><p className="mt-1">{selected.trace.stages.length} stages · {selected.trace.model_calls.length} model calls</p><p className="mt-1 break-all font-mono text-[10px]">{selected.trace.trace_id}</p></section></div> : <p className="mt-4 text-sm text-muted-foreground">选择一个 Step 查看结算和 Trace。</p>}
          </aside>
        </div>
      )}
    </StoryPage>
  )
}
