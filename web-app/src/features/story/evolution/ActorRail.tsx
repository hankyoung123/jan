import { Circle, Eye, Footprints, Sparkles, Target } from 'lucide-react'

import { useTranslation } from '@/i18n'
import type { ProjectSnapshot, SessionSnapshot } from './useSimulationSession'
import type { StepViewModel } from './simulationViewModel'

interface ActorDisplay {
  id: string
  displayName: string
  location?: string | null
  goal: string
}

function actorStatus(actorId: string, step?: StepViewModel) {
  const runningStage = Object.values(step?.stages ?? {}).find(
    (stage) => stage?.status === 'running'
  )
  if (runningStage?.stage === 'observation') return 'observing'
  if (runningStage?.actor_id === actorId) {
    if (runningStage.stage === 'actor_action') return 'acting'
    return 'thinking'
  }
  return 'idle'
}

function recentObservation(actorId: string, step?: StepViewModel) {
  const prefix = `${actorId}:`
  const line = step?.stages.observation?.summary_text
    ?.split('\n')
    .find((value) => value.startsWith(prefix))
  return line?.slice(prefix.length).trim()
}

function ActorCard({
  actor,
  session,
  step,
}: {
  actor: ActorDisplay
  session: SessionSnapshot
  step?: StepViewModel
}) {
  const { t } = useTranslation('evolution')
  const status = actorStatus(actor.id, step)
  const active = step?.actingActorId === actor.id
  const memoryCount = session.memory_snapshots[actor.id]?.record_count ?? 0
  const observation = recentObservation(actor.id, step)
  return (
    <article className={`border-l-2 p-3 ${active ? 'border-l-amber-500 bg-amber-500/8' : 'border-l-transparent'}`}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="text-sm font-semibold">{actor.displayName}</p>
          <p className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
            {t(`actorStatus.${status}`)}
          </p>
        </div>
        {active ? <Sparkles className="text-amber-500" size={15} /> : <Circle size={11} />}
      </div>
      <dl className="mt-3 space-y-1.5 text-xs text-muted-foreground">
        <div className="flex gap-2">
          <Footprints className="mt-0.5 shrink-0" size={12} />
          <dd>{actor.location || t('actor.unknownLocation')}</dd>
        </div>
        <div className="flex gap-2">
          <Target className="mt-0.5 shrink-0" size={12} />
          <dd className="line-clamp-2">{actor.goal}</dd>
        </div>
        {observation && (
          <div className="flex gap-2">
            <Eye className="mt-0.5 shrink-0" size={12} />
            <dd className="line-clamp-3">
              <span className="font-medium text-foreground">
                {t('actor.lastObservation')}:
              </span>{' '}
              {observation}
            </dd>
          </div>
        )}
      </dl>
      <p className="mt-3 font-mono text-[10px] text-muted-foreground">
        {t('actor.memories', { count: memoryCount })}
      </p>
    </article>
  )
}

export function ActorRail({
  project,
  session,
  step,
}: {
  project: ProjectSnapshot
  session: SessionSnapshot
  step?: StepViewModel
}) {
  const { t } = useTranslation('evolution')
  const projectById = new Map(project.characters.map((actor) => [actor.id, actor]))
  const dynamicById = new Map(
    session.dynamic_entities.map((actor) => [actor.entity_id, actor])
  )
  const actors: ActorDisplay[] = session.active_entity_ids.flatMap((actorId) => {
    const projectActor = projectById.get(actorId)
    if (projectActor) {
      return [{
        id: actorId,
        displayName: projectActor.display_name || actorId,
        location: projectActor.location,
        goal: projectActor.current_goal || projectActor.core_desire,
      }]
    }
    const dynamicActor = dynamicById.get(actorId)
    return dynamicActor
      ? [{
          id: actorId,
          displayName: dynamicActor.display_name,
          location: dynamicActor.location,
          goal: dynamicActor.goal,
        }]
      : []
  })
  return (
    <aside className="border bg-background">
      <header className="border-b px-4 py-3">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          {t('actors')}
        </p>
        <h2 className="mt-1 font-studio text-lg">{t('actorRail.title')}</h2>
      </header>
      <div className="divide-y">
        {actors.map((actor) => (
          <ActorCard actor={actor} key={actor.id} session={session} step={step} />
        ))}
      </div>
    </aside>
  )
}
