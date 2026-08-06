import { ArrowRight, Database, UserRound } from 'lucide-react'

import { useTranslation } from '@/i18n'
import type { StageViewModel } from './simulationViewModel'

export function MemoryRoutingView({ stage }: { stage: StageViewModel }) {
  const { t } = useTranslation('evolution')
  return (
    <div className="mt-5 border bg-muted/20 p-3">
      <p className="text-[10px] font-semibold uppercase tracking-[0.16em] text-muted-foreground">
        {t('inspector.memoryRouting')}
      </p>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        <span className="flex items-center gap-1.5 border bg-background px-2 py-1.5">
          <Database size={12} /> {stage.input_record_ids.length || 0}
        </span>
        <ArrowRight className="text-muted-foreground" size={13} />
        {stage.visible_to.length ? (
          stage.visible_to.map((actor) => (
            <span className="flex items-center gap-1.5 border bg-background px-2 py-1.5" key={actor}>
              <UserRound size={12} /> {actor}
            </span>
          ))
        ) : (
          <span className="text-muted-foreground">{t('inspector.gmOnly')}</span>
        )}
      </div>
    </div>
  )
}
