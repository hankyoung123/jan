import { Braces, Clock3, Cpu, Database, Eye, TriangleAlert } from 'lucide-react'
import type { UIMessage } from 'ai'

import { Button } from '@/components/ui/button'
import { useTranslation } from '@/i18n'
import type { StoryMessageMetadata } from '@/lib/message-capabilities'
import { AgentMessageList } from '../components/AgentMessageList'
import { MemoryRoutingView } from './MemoryRoutingView'
import type { StageViewModel } from './simulationViewModel'

function stringValue(value: unknown): string | undefined {
  return typeof value === 'string' ? value : undefined
}

export function StageInspector({
  stage,
  messages = [],
  onRetry,
}: {
  stage?: StageViewModel
  messages?: UIMessage<StoryMessageMetadata>[]
  onRetry?: () => void
}) {
  const { t } = useTranslation('evolution')
  if (!stage) {
    return (
      <aside className="border bg-background p-5 text-sm text-muted-foreground">
        {t('inspector.empty')}
      </aside>
    )
  }
  const actionSpec = stage.action_spec
  const callToAction = actionSpec ? stringValue(actionSpec.call_to_action) : undefined
  const outputType = actionSpec ? stringValue(actionSpec.output_type) : undefined
  const options = actionSpec?.options
  const observations =
    messages.length === 0 && stage.stage === 'observation' && stage.summary_text
      ? stage.summary_text.split('\n').filter(Boolean)
      : []
  return (
    <aside className="min-w-0 border bg-background">
      <header className="border-b p-4">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          {t('inspector.title')}
        </p>
        <h2 className="mt-1 font-studio text-lg">{t(`stage.${stage.stage}`)}</h2>
      </header>
      <div className="space-y-5 p-4">
        {stage.error_code && (
          <div className="border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            <div className="flex gap-2">
              <TriangleAlert className="shrink-0" size={15} />
              <span>{stage.error_code}</span>
            </div>
            {onRetry && (
              <Button className="mt-3" onClick={onRetry} size="sm" variant="outline">
                {t('inspector.retryFromCheckpoint')}
              </Button>
            )}
          </div>
        )}
        {messages.length > 0 && (
          <section className="space-y-4" data-testid="stage-agent-messages">
            <AgentMessageList messages={messages} preset="live-agent" />
          </section>
        )}
        {observations.length > 0 ? (
          <section>
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              {t('inspector.characterObservations')}
            </p>
            <div className="mt-2 space-y-2">
              {observations.map((observation) => {
                const separator = observation.indexOf(':')
                const actor = separator > 0 ? observation.slice(0, separator) : undefined
                const text = separator > 0 ? observation.slice(separator + 1).trim() : observation
                return (
                  <article className="border p-3" key={observation}>
                    {actor && <p className="mb-1 font-mono text-[10px] text-amber-600">{actor}</p>}
                    <p className="text-sm leading-6">{text}</p>
                  </article>
                )
              })}
            </div>
          </section>
        ) : messages.length === 0 && stage.summary_text && (
          <section>
            <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              {t(
                stage.stage === 'actor_action'
                  ? 'inspector.actorIntent'
                  : stage.stage === 'resolution'
                    ? 'inspector.worldResult'
                    : 'inspector.output'
              )}
            </p>
            <p className="mt-2 whitespace-pre-wrap text-sm leading-6">{stage.summary_text}</p>
          </section>
        )}
        {callToAction && (
          <section className="border bg-muted/20 p-3">
            <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
              <Braces size={12} /> {t('inspector.actionSpec')} · {outputType}
            </div>
            <p className="mt-2 text-sm leading-6">{callToAction}</p>
            {Array.isArray(options) && options.length > 0 && (
              <ul className="mt-3 grid gap-1.5">
                {options.map((option) => (
                  <li className="border bg-background px-2 py-1.5 text-xs" key={String(option)}>
                    {String(option)}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        <dl className="grid grid-cols-2 gap-2 text-xs">
          <div className="col-span-2 border p-2.5">
            <dt className="flex items-center gap-1.5 text-muted-foreground">
              <Cpu size={12} /> {t('inspector.models')}
            </dt>
            <dd className="mt-1 break-all font-mono">
              {stage.model_refs.join(', ') || t('inspector.noModel')}
            </dd>
          </div>
          <div className="border p-2.5">
            <dt className="flex items-center gap-1.5 text-muted-foreground"><Cpu size={12} /> {t('tokens')}</dt>
            <dd className="mt-1 font-mono">{stage.prompt_tokens + stage.completion_tokens}</dd>
          </div>
          <div className="border p-2.5">
            <dt className="flex items-center gap-1.5 text-muted-foreground"><Clock3 size={12} /> {t('duration')}</dt>
            <dd className="mt-1 font-mono">{stage.duration_ms ?? 0}ms</dd>
          </div>
          <div className="border p-2.5">
            <dt className="flex items-center gap-1.5 text-muted-foreground"><Database size={12} /> {t('inspector.sources')}</dt>
            <dd className="mt-1 font-mono">{stage.input_record_ids.length}</dd>
          </div>
          <div className="border p-2.5">
            <dt className="flex items-center gap-1.5 text-muted-foreground"><Eye size={12} /> {t('inspector.visibleTo')}</dt>
            <dd className="mt-1 truncate font-mono">{stage.visible_to.join(', ') || 'GM'}</dd>
          </div>
        </dl>
        {stage.stage === 'memory_routing' && <MemoryRoutingView stage={stage} />}
        {(stage.input_record_ids.length > 0 || stage.output_record_ids.length > 0) && (
          <section className="space-y-3 border-t pt-4 font-mono text-[10px] text-muted-foreground">
            <div>
              <p className="uppercase tracking-wider">{t('inspector.inputRecords')}</p>
              {stage.input_record_ids.map((id) => <p className="mt-1 break-all" key={id}>{id}</p>)}
            </div>
            <div>
              <p className="uppercase tracking-wider">{t('inspector.outputRecords')}</p>
              {stage.output_record_ids.map((id) => <p className="mt-1 break-all" key={id}>{id}</p>)}
            </div>
          </section>
        )}
      </div>
    </aside>
  )
}
