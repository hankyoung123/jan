import { CircleDot, LockKeyhole, Save, UserRound } from 'lucide-react'

import { useTranslation } from '@/i18n'
import type { SimulationViewState, StepViewModel } from './simulationViewModel'

function TimelineNode({
  step,
  selected,
  onSelect,
}: {
  step: StepViewModel
  selected: boolean
  onSelect: () => void
}) {
  const { t } = useTranslation('evolution')
  const Icon = step.checkpointId ? Save : step.actingActorId ? UserRound : LockKeyhole
  return (
    <button
      className={`min-w-48 border-t-2 px-3 py-3 text-left ${selected ? 'border-t-amber-500 bg-amber-500/8' : 'border-t-border hover:bg-muted/50'}`}
      onClick={onSelect}
      type="button"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="font-mono text-xs font-semibold">{t('step')} {step.step}</span>
        <Icon className="text-muted-foreground" size={13} />
      </div>
      <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">
        {step.summary || step.actingActorId || t(`stepStatus.${step.status}`)}
      </p>
    </button>
  )
}

export function SimulationTimeline({
  state,
  onSelect,
}: {
  state: SimulationViewState
  onSelect: (step: StepViewModel) => void
}) {
  const { t } = useTranslation('evolution')
  const steps = Object.values(state.steps).sort((left, right) => left.step - right.step)
  return (
    <section className="border bg-background">
      <header className="flex items-center gap-2 border-b px-4 py-3">
        <CircleDot size={14} />
        <h2 className="text-sm font-semibold">{t('timeline')}</h2>
      </header>
      <div className="flex overflow-x-auto">
        {steps.length ? (
          steps.map((step) => (
            <TimelineNode
              key={step.step}
              onSelect={() => onSelect(step)}
              selected={step.step === state.selectedStep}
              step={step}
            />
          ))
        ) : (
          <p className="p-4 text-sm text-muted-foreground">{t('timelineEmpty')}</p>
        )}
      </div>
    </section>
  )
}
