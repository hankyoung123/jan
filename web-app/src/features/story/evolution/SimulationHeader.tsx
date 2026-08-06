import type { components } from '@story-engine/contracts'
import { Activity, Clock3, GitBranch, Gauge } from 'lucide-react'

import { useTranslation } from '@/i18n'
import { StatusPill } from '../components/StoryLayout'

type SessionSnapshot = components['schemas']['TurnSessionSnapshot']

export function SimulationHeader({ session }: { session: SessionSnapshot }) {
  const { t } = useTranslation('evolution')
  const mode = session.request.control.mode
  const statusTone = ['failed', 'cancelled'].includes(session.status)
    ? 'danger'
    : session.status === 'running'
      ? 'success'
      : 'neutral'
  return (
    <section className="relative overflow-hidden border bg-background">
      <div className="absolute inset-y-0 left-0 w-1 bg-amber-500" />
      <div className="grid gap-4 p-5 pl-6 lg:grid-cols-[1fr_auto] lg:items-center">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3 text-sm">
          <span className="flex items-center gap-2 font-medium">
            <GitBranch size={14} /> {session.branch_id}
          </span>
          <span className="font-mono text-lg font-semibold tabular-nums">
            {t('step')} {session.current_step}
          </span>
          <StatusPill tone={statusTone}>{t(`status.${session.status}`)}</StatusPill>
          <span className="rounded-sm border px-2 py-1 text-xs uppercase tracking-[0.16em] text-muted-foreground">
            {t(`mode.${mode}`)}
          </span>
          {session.pending_control !== 'none' && (
            <span className="flex items-center gap-2 text-amber-600" role="status">
              <Activity className="animate-pulse" size={14} />
              {t(`pending.${session.pending_control}`)}
            </span>
          )}
        </div>
        <div className="flex gap-5 text-xs text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Gauge size={14} /> {session.total_model_tokens.toLocaleString()} {t('tokens')}
          </span>
          <span className="flex items-center gap-1.5">
            <Clock3 size={14} /> {t('scenes', { count: session.completed_scenes })}
          </span>
        </div>
      </div>
    </section>
  )
}
