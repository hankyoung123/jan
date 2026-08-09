import type { SimulationStage } from '@story-engine/contracts'
import {
  Bot,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  LoaderCircle,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { StoryModelMessage } from '../modelMessages'
import {
  simulationStages,
  type SimulationViewState,
  type StageViewModel,
} from '../evolution/simulationViewModel'

const stageLabels: Record<SimulationStage, string> = {
  termination: 'Termination',
  observation: 'Observation',
  actor_selection: 'Actor Selection',
  action_spec: 'Action Spec',
  actor_action: 'Actor Action',
  resolution: 'Resolution',
  memory_routing: 'Memory Routing',
  promotion: 'Promotion',
  commit: 'Commit',
}

type AgentConsoleProps = {
  viewState: SimulationViewState
  collapsed: boolean
  onCollapsedChange: (collapsed: boolean) => void
}

function formatDuration(seconds?: number) {
  if (seconds == null) return '—'
  return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(2)} s`
}

function formatContent(value: unknown) {
  if (typeof value === 'string') return value
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

function textParts(message: StoryModelMessage, type: 'text' | 'reasoning') {
  return message.parts
    .filter((part) => part.type === type && 'text' in part)
    .map((part) => ('text' in part ? part.text : ''))
    .join('')
}

function roleLabel(message: StoryModelMessage, stage: StageViewModel) {
  const metadata = message.metadata
  if (metadata?.agentType === 'game_master') return 'GM'
  const identity = (metadata?.agentName ?? stage.actor_id ?? '').toLowerCase()
  return identity === 'player' || identity.includes('player') ? 'Player' : 'Actor'
}

function statusLabel(status: StageViewModel['status']) {
  if (status === 'running') return 'running'
  if (status === 'failed') return 'failed'
  if (status === 'succeeded') return 'succeeded'
  return status
}

function stageAgentLabel(stage: StageViewModel, messages: StoryModelMessage[]) {
  if (stage.stage === 'resolution') return 'GM'
  const tracedName = messages[0]?.metadata?.agentName
  if (tracedName) return tracedName
  const actorId = stage.actor_id ?? ''
  if (stage.stage === 'actor_action' && actorId.toLowerCase().includes('player')) {
    return 'Player'
  }
  return actorId || (stage.stage === 'actor_action' ? 'Actor' : 'GM')
}

function stageTaskLabel(stage: StageViewModel) {
  const actorId = stage.actor_id ?? ''
  if (stage.stage === 'actor_action' && actorId.toLowerCase().includes('player')) {
    return 'Intent'
  }
  return stageLabels[stage.stage]
}

function StatusIcon({ status }: { status: StageViewModel['status'] }) {
  if (status === 'running') {
    return <LoaderCircle aria-label="running" className="animate-spin text-sky-500" size={13} />
  }
  if (status === 'failed') {
    return <CircleAlert aria-label="failed" className="text-destructive" size={13} />
  }
  return <Check aria-label={statusLabel(status)} className="text-emerald-500" size={13} />
}

function ModelCall({ message, stage }: { message: StoryModelMessage; stage: StageViewModel }) {
  const metadata = message.metadata
  const output = textParts(message, 'text')
  const reasoning = textParts(message, 'reasoning')
  const running = metadata?.outputStatus === 'streaming'
  const failed = metadata?.outputStatus === 'failed'
  const inputs = metadata?.inputMessages ?? []

  return (
    <details className="group border-t border-border/60 px-3 py-2 first:border-t-0">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-xs">
        <span className="min-w-0 truncate font-medium">
          {metadata?.agentName ?? stage.actor_id ?? 'Story Engine'}
          <span className="ml-1 font-normal text-muted-foreground">· {roleLabel(message, stage)}</span>
        </span>
        <span className="flex shrink-0 items-center gap-1.5 text-[10px] text-muted-foreground">
          {running && <LoaderCircle className="animate-spin text-sky-500" size={11} />}
          {failed && <CircleAlert className="text-destructive" size={11} />}
          {metadata?.model ?? 'model'}
        </span>
      </summary>

      <div className="mt-3 space-y-3 text-[11px]">
        <section>
          <h5 className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">Input / Context</h5>
          {inputs.length ? (
            <div className="space-y-2">
              {inputs.map((input, index) => (
                <div className="rounded-sm bg-muted/60 p-2" key={`${input.role}:${index}`}>
                  <div className="mb-1 font-medium uppercase text-muted-foreground">{input.role}</div>
                  <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words font-mono">{formatContent(input.content)}</pre>
                </div>
              ))}
              {metadata?.outputSchema && (
                <details>
                  <summary className="cursor-pointer text-muted-foreground">Output schema</summary>
                  <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-sm bg-muted/60 p-2 font-mono">{metadata.outputSchema}</pre>
                </details>
              )}
            </div>
          ) : (
            <p className="text-muted-foreground">No request context in this trace.</p>
          )}
        </section>

        {reasoning && (
          <details>
            <summary className="cursor-pointer font-semibold uppercase tracking-wide text-muted-foreground">Reasoning</summary>
            <pre className="mt-1 max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-sm bg-muted/60 p-2 font-mono">{reasoning}</pre>
          </details>
        )}

        <section>
          <h5 className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">Output</h5>
          <pre
            aria-live="polite"
            className={`max-h-64 overflow-auto whitespace-pre-wrap break-words rounded-sm p-2 font-mono ${failed ? 'bg-destructive/10 text-destructive' : 'bg-muted/60'}`}
          >
            {output || (failed ? metadata?.error : running ? 'Waiting for model output…' : 'No text output.')}
          </pre>
        </section>

        {stage.stage === 'resolution' && stage.summary_text && (
          <section>
            <h5 className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">Resolved Event</h5>
            <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words rounded-sm bg-emerald-500/10 p-2 font-mono">{stage.summary_text}</pre>
          </section>
        )}

        <section>
          <h5 className="mb-1 font-semibold uppercase tracking-wide text-muted-foreground">Model Info</h5>
          <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-muted-foreground">
            <dt>Model</dt><dd className="truncate text-foreground" title={metadata?.model}>{metadata?.model ?? '—'}</dd>
            <dt>Duration</dt><dd className="text-foreground">{formatDuration(metadata?.duration)}</dd>
            <dt>Input tokens</dt><dd className="text-foreground">{metadata?.promptTokens ?? 0}</dd>
            <dt>Output tokens</dt><dd className="text-foreground">{metadata?.completionTokens ?? 0}</dd>
            <dt>Reasoning tokens</dt><dd className="text-foreground">{metadata?.reasoningTokens ?? 0}</dd>
            <dt>Retries</dt><dd className="text-foreground">{metadata?.retryCount ?? 0}</dd>
          </dl>
          {metadata?.error && <p className="mt-2 break-words text-destructive">{metadata.error}</p>}
        </section>
      </div>
    </details>
  )
}

function StageEntry({ stage, messages }: { stage: StageViewModel; messages: StoryModelMessage[] }) {
  return (
    <article className={`overflow-hidden rounded-md border bg-background ${stage.status === 'running' ? 'border-sky-500/60 shadow-[0_0_0_1px_rgb(14_165_233_/_0.15)]' : ''}`}>
      <div className="flex items-center justify-between gap-3 px-3 py-2 text-xs">
        <div className="min-w-0">
          <div className="truncate font-medium">{stageAgentLabel(stage, messages)}</div>
          <div className="text-[10px] text-muted-foreground">{stageTaskLabel(stage)}</div>
        </div>
        <div className="flex shrink-0 items-center gap-1.5 text-[10px] text-muted-foreground">
          <StatusIcon status={stage.status} />
          <span>{statusLabel(stage.status)}</span>
          {stage.duration_ms != null && <span>· {formatDuration(stage.duration_ms / 1000)}</span>}
        </div>
      </div>
      {messages.map((message) => <ModelCall key={message.id} message={message} stage={stage} />)}
      {stage.status === 'failed' && stage.error_code && messages.length === 0 && (
        <p className="border-t border-border/60 px-3 py-2 text-[11px] text-destructive">{stage.error_code}</p>
      )}
      {stage.stage === 'resolution' && stage.summary_text && messages.length === 0 && (
        <pre className="max-h-52 overflow-auto whitespace-pre-wrap break-words border-t border-border/60 bg-emerald-500/10 px-3 py-2 text-[11px]">{stage.summary_text}</pre>
      )}
    </article>
  )
}

export function AgentConsole({ viewState, collapsed, onCollapsedChange }: AgentConsoleProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const steps = useMemo(
    () => Object.values(viewState.steps).sort((left, right) => left.step - right.step),
    [viewState.steps]
  )
  const latestStep = steps.at(-1)?.step
  const [openSteps, setOpenSteps] = useState<Set<number>>(new Set())

  useEffect(() => {
    if (latestStep != null) {
      setOpenSteps((current) => current.has(latestStep) ? current : new Set([...current, latestStep]))
    }
  }, [latestStep])

  useEffect(() => {
    if (!collapsed) scrollRef.current?.scrollTo?.({ top: scrollRef.current.scrollHeight })
  }, [collapsed, viewState.lastSequence])

  if (collapsed) {
    return (
      <aside className="flex h-full min-h-56 items-start justify-center border-l bg-background pt-3">
        <button aria-label="展开 Agent Console" className="grid size-8 place-items-center rounded-sm hover:bg-accent" onClick={() => onCollapsedChange(false)} type="button">
          <ChevronLeft size={16} />
        </button>
      </aside>
    )
  }

  return (
    <aside className="flex min-h-[36rem] flex-col overflow-hidden border bg-muted/20" data-testid="agent-console">
      <header className="flex h-11 shrink-0 items-center justify-between border-b bg-background px-3">
        <div className="flex items-center gap-2">
          <Bot size={15} />
          <h2 className="text-xs font-semibold uppercase tracking-[0.12em]">Agent Console</h2>
        </div>
        <button aria-label="折叠 Agent Console" className="grid size-7 place-items-center rounded-sm hover:bg-accent" onClick={() => onCollapsedChange(true)} type="button">
          <ChevronRight size={15} />
        </button>
      </header>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3" ref={scrollRef}>
        {steps.length === 0 && <p className="py-12 text-center text-xs text-muted-foreground">Waiting for engine events…</p>}
        {steps.map((step) => {
          const orderedStages = simulationStages.flatMap((stageName) => {
            const stage = step.stages[stageName]
            return stage ? [stage] : []
          })
          return (
            <details
              key={step.step}
              onToggle={(event) => {
                const isOpen = event.currentTarget.open
                setOpenSteps((current) => {
                  const next = new Set(current)
                  if (isOpen) next.add(step.step)
                  else next.delete(step.step)
                  return next
                })
              }}
              open={openSteps.has(step.step)}
            >
              <summary className="flex cursor-pointer list-none items-center justify-between border-b pb-1.5 text-xs font-semibold">
                <span>Step {step.step}</span>
                <span className={step.status === 'failed' ? 'text-destructive' : 'text-muted-foreground'}>{step.status}</span>
              </summary>
              <div className="mt-2 space-y-2">
                {orderedStages.map((stage) => (
                  <StageEntry
                    key={stage.executionEventId}
                    messages={stage.messageIds.flatMap((id) => viewState.messages[id] ? [viewState.messages[id]] : [])}
                    stage={stage}
                  />
                ))}
              </div>
            </details>
          )
        })}
      </div>
    </aside>
  )
}
