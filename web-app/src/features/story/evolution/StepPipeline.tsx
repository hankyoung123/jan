import type { SimulationStage } from '@story-engine/contracts'

import { useTranslation } from '@/i18n'
import { simulationStages, type StepViewModel } from './simulationViewModel'
import { StageNode } from './StageNode'

interface Props {
  step?: StepViewModel
  selectedStage?: SimulationStage
  onSelect: (stage: SimulationStage) => void
}

export function StepPipeline({ step, selectedStage, onSelect }: Props) {
  const { t } = useTranslation('evolution')
  return (
    <section className="min-w-0 border bg-background">
      <header className="flex items-center justify-between border-b px-4 py-3">
        <div>
          <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            {t('pipeline.eyebrow')}
          </p>
          <h2 className="mt-1 font-studio text-lg">
            {step ? `${t('step')} ${step.step}` : t('pipeline.waiting')}
          </h2>
        </div>
        {step && (
          <span className="font-mono text-xs uppercase text-muted-foreground">
            {t(`stepStatus.${step.status}`)}
          </span>
        )}
      </header>
      <div className="divide-y">
        {simulationStages.map((stage) => (
          <StageNode
            key={stage}
            onSelect={() => onSelect(stage)}
            selected={selectedStage === stage}
            stage={stage}
            value={step?.stages[stage]}
          />
        ))}
      </div>
    </section>
  )
}
