import type { SimulationStage } from '@story-engine/contracts'
import { Check, Circle, LoaderCircle, OctagonX, SkipForward, X } from 'lucide-react'

import { useTranslation } from '@/i18n'
import type { StageViewModel } from './simulationViewModel'

interface Props {
  stage: SimulationStage
  value?: StageViewModel
  selected: boolean
  onSelect: () => void
}

export function StageNode({ stage, value, selected, onSelect }: Props) {
  const { t } = useTranslation('evolution')
  const status = value?.status ?? 'pending'
  const Icon =
    status === 'running'
      ? LoaderCircle
      : status === 'succeeded'
        ? Check
        : status === 'failed'
          ? OctagonX
          : status === 'cancelled'
            ? X
            : status === 'skipped'
              ? SkipForward
              : Circle
  return (
    <button
      aria-pressed={selected}
      className={`group grid w-full grid-cols-[24px_1fr_auto] items-start gap-3 border-l-2 px-3 py-3 text-left transition-colors ${
        selected
          ? 'border-l-amber-500 bg-amber-500/8'
          : 'border-l-transparent hover:bg-muted/50'
      }`}
      onClick={onSelect}
      type="button"
    >
      <Icon
        className={status === 'running' ? 'animate-spin text-amber-500' : 'text-muted-foreground'}
        size={16}
      />
      <span className="min-w-0">
        <span className="block text-sm font-medium">{t(`stage.${stage}`)}</span>
        {value?.summary_text && (
          <span className="mt-1 line-clamp-2 block text-xs leading-5 text-muted-foreground">
            {value.summary_text}
          </span>
        )}
      </span>
      <span className="font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {value?.duration_ms != null ? `${value.duration_ms}ms` : t(`stageStatus.${status}`)}
      </span>
    </button>
  )
}
