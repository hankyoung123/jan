import type {
  components,
  EngineEventEnvelope,
  SimulationStage,
  SimulationStageEventPayload,
} from '@story-engine/contracts'

import type { ViewLocation } from './viewUrl'

type SimulationLogRecord = components['schemas']['SimulationLogRecord']

export const simulationStages: SimulationStage[] = [
  'termination',
  'observation',
  'actor_selection',
  'action_spec',
  'actor_action',
  'resolution',
  'memory_routing',
  'promotion',
  'commit',
]

export interface StageViewModel extends SimulationStageEventPayload {
  sequence: number
}

export interface StepViewModel {
  step: number
  status: 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
  stages: Partial<Record<SimulationStage, StageViewModel>>
  actingActorId?: string
  summary?: string
  checkpointId?: string
}

export interface SimulationViewState {
  steps: Record<number, StepViewModel>
  selectedStep: number
  selectedStage?: SimulationStage
  lastSequence: number
}

export const initialSimulationViewState: SimulationViewState = {
  steps: {},
  selectedStep: 0,
  lastSequence: 0,
}

function elapsedMilliseconds(startedAt: string, completedAt?: string | null): number | undefined {
  if (!completedAt) return undefined
  return Math.max(0, Date.parse(completedAt) - Date.parse(startedAt))
}

/** Rebuild the durable part of the dashboard from the append-only simulation log. */
export function restoreSimulationTrace(
  records: SimulationLogRecord[],
  sessionId: string,
  projectId: string,
  sessionStatus?: string,
  currentStep?: number
): SimulationViewState {
  const steps: Record<number, StepViewModel> = {}
  for (const record of records) {
    const trace = record.trace
    if (trace.session_id !== sessionId) continue
    if (
      currentStep !== undefined &&
      (trace.step > currentStep ||
        (trace.step === currentStep &&
          trace.status === 'failed' &&
          sessionStatus !== 'failed'))
    ) {
      continue
    }
    const stages: StepViewModel['stages'] = {}
    const callsById = new Map(trace.model_calls.map((call) => [call.call_id, call]))
    for (const value of trace.stages) {
      const stage = value.stage_type as SimulationStage
      if (!simulationStages.includes(stage)) continue
      const calls = value.model_call_ids.flatMap((id) => {
        const call = callsById.get(id)
        return call ? [call] : []
      })
      stages[stage] = {
        event_id: value.stage_id,
        project_id: projectId,
        session_id: trace.session_id,
        branch_id: trace.branch_id,
        step: trace.step,
        stage,
        status: value.status,
        actor_id: value.actor_id ?? trace.acting_actor_id ?? null,
        action_spec:
          value.action_spec ??
          (stage === 'action_spec' && trace.action_spec
            ? { ...trace.action_spec }
            : null),
        summary_text:
          value.detail_text ??
          (stage === 'resolution'
            ? record.result.resolved_turn?.raw_resolution_text ?? null
            : null),
        input_record_ids: value.input_record_ids,
        output_record_ids: value.output_record_ids,
        visible_to: value.visible_to,
        profile_ids: [...new Set(calls.map((call) => call.profile_id))],
        model_refs: [
          ...new Set(
            calls.flatMap((call) => (call.model_ref ? [call.model_ref] : []))
          ),
        ],
        prompt_tokens:
          value.prompt_tokens ||
          calls.reduce((total, call) => total + call.prompt_tokens, 0),
        completion_tokens:
          value.completion_tokens ||
          calls.reduce((total, call) => total + call.completion_tokens, 0),
        duration_ms:
          value.duration_ms ??
          elapsedMilliseconds(value.started_at, value.completed_at) ??
          null,
        checkpoint_id:
          value.checkpoint_id ??
          (stage === 'commit' ? record.checkpoint_id ?? null : null),
        error_code: value.error_code ?? null,
        started_at: value.started_at,
        completed_at: value.completed_at ?? null,
        sequence: 0,
      }
    }
    steps[trace.step] = {
      step: trace.step,
      status:
        trace.status === 'failed'
          ? 'failed'
          : trace.status === 'cancelled'
            ? 'cancelled'
            : 'completed',
      stages,
      actingActorId: trace.acting_actor_id ?? undefined,
      summary: record.result.resolved_turn?.raw_resolution_text ?? undefined,
      checkpointId: record.checkpoint_id ?? undefined,
    }
  }
  const selectedStep = Math.max(0, ...Object.keys(steps).map(Number))
  return {
    steps,
    selectedStep,
    selectedStage: selectedStep ? 'resolution' : undefined,
    lastSequence: 0,
  }
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === 'string')
}

export function isStageEventPayload(
  value: unknown
): value is SimulationStageEventPayload {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Record<string, unknown>
  return (
    typeof candidate.event_id === 'string' &&
    typeof candidate.project_id === 'string' &&
    typeof candidate.session_id === 'string' &&
    typeof candidate.branch_id === 'string' &&
    typeof candidate.step === 'number' &&
    simulationStages.includes(candidate.stage as SimulationStage) &&
    typeof candidate.status === 'string' &&
    isStringArray(candidate.input_record_ids) &&
    isStringArray(candidate.output_record_ids) &&
    isStringArray(candidate.visible_to) &&
    isStringArray(candidate.profile_ids) &&
    isStringArray(candidate.model_refs) &&
    typeof candidate.started_at === 'string'
  )
}

function stepFor(state: SimulationViewState, step: number): StepViewModel {
  return (
    state.steps[step] ?? {
      step,
      status: 'pending',
      stages: {},
    }
  )
}

export function reduceSimulationEvent(
  state: SimulationViewState,
  event: EngineEventEnvelope
): SimulationViewState {
  if (event.sequence <= state.lastSequence) return state
  const next: SimulationViewState = {
    ...state,
    lastSequence: event.sequence,
  }

  if (
    event.type === 'simulation.stage.started' ||
    event.type === 'simulation.stage.completed' ||
    event.type === 'simulation.stage.failed'
  ) {
    if (!isStageEventPayload(event.payload)) return next
    const current = stepFor(state, event.payload.step)
    const stage: StageViewModel = { ...event.payload, sequence: event.sequence }
    const failed = stage.status === 'failed'
    const cancelled = stage.status === 'cancelled'
    const updated: StepViewModel = {
      ...current,
      status: failed ? 'failed' : cancelled ? 'cancelled' : 'running',
      actingActorId: stage.actor_id ?? current.actingActorId,
      summary:
        stage.stage === 'resolution' && stage.summary_text
          ? stage.summary_text
          : current.summary,
      checkpointId: stage.checkpoint_id ?? current.checkpointId,
      stages: { ...current.stages, [stage.stage]: stage },
    }
    return {
      ...next,
      steps: { ...state.steps, [stage.step]: updated },
      selectedStep: Math.max(state.selectedStep, stage.step),
      selectedStage: state.selectedStage ?? stage.stage,
    }
  }

  if (
    event.type === 'simulation.started' &&
    event.payload.restored === true &&
    typeof event.payload.step === 'number'
  ) {
    const restoredStep = event.payload.step
    const steps = Object.fromEntries(
      Object.entries(state.steps).filter(([step]) => Number(step) < restoredStep)
    )
    const selectedStep = Math.max(0, ...Object.keys(steps).map(Number))
    return {
      ...next,
      steps,
      selectedStep,
      selectedStage: selectedStep ? state.selectedStage : undefined,
    }
  }

  const stepValue = event.payload.step
  if (typeof stepValue !== 'number') return next
  const current = stepFor(state, stepValue)
  if (event.type === 'simulation.step.started') {
    return {
      ...next,
      steps: {
        ...state.steps,
        [stepValue]: { ...current, status: 'running' },
      },
      selectedStep: stepValue,
    }
  }
  if (event.type === 'simulation.step.completed') {
    return {
      ...next,
      steps: {
        ...state.steps,
        [stepValue]: {
          ...current,
          status: event.payload.status === 'cancelled' ? 'cancelled' : 'completed',
          actingActorId:
            typeof event.payload.acting_actor_id === 'string'
              ? event.payload.acting_actor_id
              : current.actingActorId,
        },
      },
      selectedStep: stepValue,
    }
  }
  return next
}

export function selectStage(
  state: SimulationViewState,
  step: number,
  stage: SimulationStage
): SimulationViewState {
  return { ...state, selectedStep: step, selectedStage: stage }
}

export function selectViewLocation(
  state: SimulationViewState,
  location: ViewLocation
): SimulationViewState {
  const step =
    location.step !== undefined && state.steps[location.step]
      ? location.step
      : state.selectedStep
  const available = Object.keys(
    state.steps[step]?.stages ?? {}
  ) as SimulationStage[]
  const fallback = available.includes('resolution') ? 'resolution' : available[0]
  const selectedStage =
    location.stage && available.includes(location.stage)
      ? location.stage
      : state.selectedStage !== undefined &&
          available.includes(state.selectedStage)
        ? state.selectedStage
        : fallback
  return { ...state, selectedStep: step, selectedStage }
}
