import type { SimulationStage } from '@story-engine/contracts'
import { Link } from '@tanstack/react-router'
import { ArrowRight, Play, RotateCcw } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { route } from '@/constants/routes'
import { useTranslation } from '@/i18n'
import { useActiveStoryProjectId } from '../activeProject'
import { PageHeader, primaryButton, StoryPage } from '../components/StoryLayout'
import { ActorRail } from './ActorRail'
import { BranchNavigator } from './BranchNavigator'
import { SimulationControls } from './SimulationControls'
import { SimulationHeader } from './SimulationHeader'
import { SimulationTimeline } from './SimulationTimeline'
import { StageInspector } from './StageInspector'
import { StepPipeline } from './StepPipeline'
import type { StepViewModel } from './simulationViewModel'
import {
  type ControlMode,
  type ProjectSnapshot,
  useSimulationSession,
} from './useSimulationSession'
import { useSimulationStream } from './useSimulationStream'

const CONTROL_MODES: ControlMode[] = ['step', 'scene', 'chapter', 'autonomous']

function SessionSetup({
  project,
  initialBranch,
  pending,
  onStart,
}: {
  project: ProjectSnapshot
  initialBranch: string
  pending: boolean
  onStart: (options: {
    branchId: string
    premise: string
    actorIds: string[]
    contentLocale: string
    mode: ControlMode
  }) => void
}) {
  const { t } = useTranslation('evolution')
  const initialIncident = project.world.world_variables?.initial_incident
  const [premise, setPremise] = useState(
    typeof initialIncident === 'string' ? initialIncident : ''
  )
  const [branchId, setBranchId] = useState(initialBranch)
  const [contentLocale, setContentLocale] = useState('zh-CN')
  const [mode, setMode] = useState<ControlMode>('scene')
  const [selectedActors, setSelectedActors] = useState<string[]>([])
  const activeActors = project.characters.filter((actor) => actor.type === 'active')

  return (
    <section className="border bg-background">
      <header className="border-b p-5">
        <p className="text-[10px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
          {t('setup.eyebrow')}
        </p>
        <h2 className="mt-1 font-studio text-xl">{t('setup.title')}</h2>
      </header>
      <div className="grid gap-6 p-5 lg:grid-cols-2">
        <div className="space-y-4">
          <label className="block text-sm font-medium" htmlFor="simulation-premise">
            {t('setup.premise')}
          </label>
          <textarea
            className="min-h-32 w-full resize-y border bg-background p-3 text-sm outline-none focus:border-amber-500"
            id="simulation-premise"
            onChange={(event) => setPremise(event.target.value)}
            value={premise}
          />
          <div className="grid gap-4 sm:grid-cols-3">
            <label className="text-xs font-medium" htmlFor="simulation-mode">
              {t('setup.mode')}
              <select
                className="mt-2 h-9 w-full border bg-background px-2 text-sm"
                id="simulation-mode"
                onChange={(event) => setMode(event.target.value as ControlMode)}
                value={mode}
              >
                {CONTROL_MODES.map((value) => (
                  <option key={value} value={value}>{t(`mode.${value}`)}</option>
                ))}
              </select>
            </label>
            <label className="text-xs font-medium" htmlFor="simulation-branch">
              {t('setup.branch')}
              <Input
                className="mt-2"
                id="simulation-branch"
                onChange={(event) => setBranchId(event.target.value)}
                value={branchId}
              />
            </label>
            <label className="text-xs font-medium" htmlFor="content-locale">
              {t('locale')}
              <Input
                className="mt-2"
                id="content-locale"
                onChange={(event) => setContentLocale(event.target.value)}
                value={contentLocale}
              />
            </label>
          </div>
        </div>
        <fieldset>
          <legend className="text-sm font-medium">{t('setup.actors')}</legend>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {t('setup.actorsHint')}
          </p>
          <div className="mt-3 grid gap-2 sm:grid-cols-2">
            {activeActors.map((actor) => (
              <label className="flex items-center gap-2 border p-3 text-sm" key={actor.id}>
                <input
                  checked={selectedActors.includes(actor.id)}
                  onChange={() =>
                    setSelectedActors((current) =>
                      current.includes(actor.id)
                        ? current.filter((id) => id !== actor.id)
                        : [...current, actor.id]
                    )
                  }
                  type="checkbox"
                />
                <span>
                  <span className="block font-medium">{actor.display_name || actor.id}</span>
                  <span className="mt-0.5 block text-[10px] text-muted-foreground">
                    {actor.location || t('actor.unknownLocation')}
                  </span>
                </span>
              </label>
            ))}
          </div>
          <p className="mt-3 border-l-2 border-l-amber-500 pl-3 text-xs text-muted-foreground">
            {selectedActors.length === 0
              ? t('setup.automaticParticipants')
              : t('setup.selectedParticipants', { count: selectedActors.length })}
          </p>
        </fieldset>
      </div>
      <footer className="flex justify-end border-t p-4">
        <Button
          disabled={pending || !premise.trim() || !branchId.trim()}
          onClick={() =>
            onStart({
              branchId: branchId.trim(),
              premise: premise.trim(),
              actorIds: selectedActors,
              contentLocale: contentLocale.trim(),
              mode,
            })
          }
        >
          <Play size={15} /> {t('setup.start')}
        </Button>
      </footer>
    </section>
  )
}

export function EvolutionView() {
  const { t } = useTranslation('evolution')
  const projectId = useActiveStoryProjectId()
  const simulation = useSimulationSession(projectId ?? undefined)
  const [notice, setNotice] = useState<string | null>(null)
  const { viewState, chooseStage } = useSimulationStream({
    projectId: projectId ?? undefined,
    sessionId: simulation.session?.session_id,
    sessionStatus: simulation.session?.status,
    currentStep: simulation.session?.current_step,
    branchId: simulation.session?.branch_id,
    onSessionChanged: simulation.refreshSession,
  })

  const selectedStep = viewState.steps[viewState.selectedStep]
  const selectedStage = viewState.selectedStage
    ? selectedStep?.stages[viewState.selectedStage]
    : undefined
  const retryCheckpointId =
    selectedStage?.status === 'failed' && simulation.session?.status === 'failed'
      ? simulation.session?.checkpoint_id ?? undefined
      : undefined
  const ended = simulation.session
    ? ['terminated', 'cancelled', 'failed', 'interrupted'].includes(
        simulation.session.status
      )
    : false
  const urlBranch =
    typeof window === 'undefined'
      ? 'main'
      : new URLSearchParams(window.location.search).get('branch') || 'main'

  useEffect(() => {
    if (simulation.error) setNotice(null)
  }, [simulation.error])

  const selectTimelineStep = (step: StepViewModel) => {
    const preferred: SimulationStage = step.stages.resolution
      ? 'resolution'
      : (Object.keys(step.stages)[0] as SimulationStage | undefined) ?? 'termination'
    chooseStage(step.step, preferred)
  }

  const eyebrow = useMemo(
    () =>
      simulation.project
        ? `${simulation.project.project.title} / ${simulation.session?.branch_id ?? 'main'}`
        : t('loading'),
    [simulation.project, simulation.session?.branch_id, t]
  )

  if (!projectId) {
    return (
      <StoryPage>
        <PageHeader eyebrow={t('workspace')} title={t('title')} />
        <section className="border bg-background p-8">
          <h2 className="font-studio text-xl">{t('noProject')}</h2>
          <Link className={`${primaryButton} mt-5`} to={route.submission}>
            {t('goToSubmission')} <ArrowRight size={15} />
          </Link>
        </section>
      </StoryPage>
    )
  }

  return (
    <StoryPage wide>
      <PageHeader
        action={
          <div className="flex items-center gap-2">
            {simulation.sessions.length > 0 && (
              <select
                aria-label="历史会话"
                className="h-9 max-w-56 border bg-background px-2 font-mono text-xs"
                onChange={(event) => void simulation.selectSession(event.target.value)}
                value={simulation.session?.session_id ?? ''}
              >
                {simulation.sessions.map((item) => (
                  <option key={item.session_id} value={item.session_id}>
                    {item.branch_id} · S{item.current_step} · {item.status}
                  </option>
                ))}
              </select>
            )}
            {ended && (
              <Button onClick={simulation.startNew} variant="outline">
                <RotateCcw size={14} /> {t('newSession')}
              </Button>
            )}
          </div>
        }
        eyebrow={eyebrow}
        title={t('title')}
      />
      {simulation.error && (
        <p className="mb-4 border border-destructive p-3 text-sm text-destructive" role="alert">
          {simulation.error}
        </p>
      )}
      {notice && (
        <p className="mb-4 border border-emerald-500/30 bg-emerald-500/8 p-3 text-sm text-emerald-700" role="status">
          {notice}
        </p>
      )}
      {!simulation.session ? (
        simulation.project && (
          <SessionSetup
            initialBranch={urlBranch}
            onStart={(options) => void simulation.start(options)}
            pending={simulation.isPending('start')}
            project={simulation.project}
          />
        )
      ) : (
          <div className="space-y-4">
          {simulation.session.restoration_notice_text && (
            <p className="border-l-2 border-amber-500 bg-amber-500/8 p-3 text-sm text-amber-800">
              {simulation.session.restoration_notice_text}
            </p>
          )}
          <div>
            <SimulationHeader session={simulation.session} />
            <SimulationControls
              onCancel={() => void simulation.control('cancel')}
              onCheckpoint={() => void simulation.checkpoint()}
              onPause={() => void simulation.control('pause')}
              onResume={() => void simulation.control('resume')}
              onRun={() => void simulation.control('run')}
              onStep={() => void simulation.step()}
              onTerminate={() => void simulation.control('terminate')}
              pending={simulation.isPending}
              status={simulation.session.status}
            />
          </div>
          {simulation.project && (
            <div className="grid min-h-[560px] gap-4 xl:grid-cols-[260px_minmax(360px,1fr)_minmax(300px,0.85fr)]">
              <ActorRail
                project={simulation.project}
                session={simulation.session}
                step={selectedStep}
              />
              <StepPipeline
                onSelect={(stage) => chooseStage(viewState.selectedStep, stage)}
                selectedStage={viewState.selectedStage}
                step={selectedStep}
              />
              <StageInspector
                onRetry={
                  retryCheckpointId
                    ? () => void simulation.restore(retryCheckpointId)
                    : undefined
                }
                stage={selectedStage}
              />
            </div>
          )}
          <SimulationTimeline state={viewState} onSelect={selectTimelineStep} />
          <BranchNavigator
            branches={simulation.branches}
            onCompare={simulation.compareBranches}
            onFork={async (branchId) => {
              const result = await simulation.fork(branchId)
              if (result) setNotice(t('notice.branchCreated', { branch: branchId }))
              return result
            }}
            onLocale={async (locale) => {
              const result = await simulation.switchLocale(locale)
              if (result) setNotice(t('notice.localeChanged', { locale }))
              return result
            }}
            onProjection={async () => {
              const result = await simulation.rebuildProjection()
              if (result) setNotice(t('notice.projectionBuilt'))
              return result
            }}
            onRestore={async (checkpointId) => {
              const result = await simulation.restore(checkpointId)
              if (result) setNotice(t('notice.restored'))
              return result
            }}
            pending={simulation.isPending}
            session={simulation.session}
          />
        </div>
      )}
    </StoryPage>
  )
}
