import type { components, EngineEventEnvelope } from '@story-engine/contracts'
import { describe, expect, it } from 'vitest'

import {
  initialSimulationViewState,
  reduceSimulationEvent,
  restoreSimulationTrace,
} from './simulationViewModel'

function event(
  sequence: number,
  type: EngineEventEnvelope['type'],
  payload: Record<string, unknown>
): EngineEventEnvelope {
  return {
    event_id: `01J0000000000000000000000${sequence}`.slice(0, 26),
    project_id: 'north-star',
    subject_id: 'session:one',
    timestamp: '2026-08-02T00:00:00Z',
    sequence,
    type,
    payload,
  }
}

const stagePayload = {
  event_id: 'stage-event:1',
  project_id: 'north-star',
  session_id: 'session:one',
  branch_id: 'main',
  step: 2,
  stage: 'actor_selection',
  status: 'succeeded',
  actor_id: 'ara',
  action_spec: null,
  summary_text: 'Ara was selected.',
  input_record_ids: ['observation:2:ara'],
  output_record_ids: [],
  visible_to: ['ara'],
  profile_ids: ['game-master'],
  provider_ids: ['local'],
  model_ids: ['replay-model'],
  prompt_tokens: 10,
  completion_tokens: 2,
  duration_ms: 45,
  checkpoint_id: null,
  error_code: null,
  started_at: '2026-08-02T00:00:00Z',
  completed_at: '2026-08-02T00:00:00.045Z',
}

describe('simulationViewModel', () => {
  it('aggregates stage events and ignores duplicate sequences', () => {
    const selected = reduceSimulationEvent(
      initialSimulationViewState,
      event(3, 'simulation.stage.completed', stagePayload)
    )
    const duplicate = reduceSimulationEvent(
      selected,
      event(3, 'simulation.stage.completed', {
        ...stagePayload,
        actor_id: 'bo',
      })
    )

    expect(selected.steps[2].actingActorId).toBe('ara')
    expect(selected.steps[2].stages.actor_selection?.duration_ms).toBe(45)
    expect(duplicate).toBe(selected)

    const restored = reduceSimulationEvent(
      selected,
      event(4, 'simulation.started', {
        session_id: 'session:one',
        checkpoint_id: `checkpoint-${'a'.repeat(64)}`,
        restored: true,
        step: 2,
        status: 'paused',
      })
    )
    expect(restored.steps).toEqual({})
  })

  it('rebuilds a completed causal pipeline from durable trace records', () => {
    const record = {
      schema_version: 1,
      checkpoint_id: `checkpoint-${'a'.repeat(64)}`,
      state_hash: 'a'.repeat(64),
      result: {
        session_id: 'session:one',
        branch_id: 'main',
        step: 0,
        acting_actor_id: 'ara',
        action_spec: null,
        action_text: 'Inspect the antenna.',
        resolved_turn: null,
        status: 'paused',
        checkpoint_id: null,
        boundary: 'none',
      },
      trace: {
        trace_id: 'trace:one',
        session_id: 'session:one',
        branch_id: 'main',
        step: 0,
        content_locale: 'en-US',
        stages: [
          {
            stage_id: 'stage:commit',
            stage_type: 'commit',
            started_at: '2026-08-02T00:00:00Z',
            completed_at: '2026-08-02T00:00:00.010Z',
            status: 'succeeded',
            model_call_ids: [],
            input_record_ids: [],
            output_record_ids: [],
            detail_text: 'Committed',
          },
        ],
        model_calls: [],
        action_spec: null,
        acting_actor_id: 'ara',
        putative_event_record_id: null,
        resolved_event_record_ids: [],
        started_at: '2026-08-02T00:00:00Z',
        completed_at: '2026-08-02T00:00:00.010Z',
        status: 'succeeded',
      },
    } as components['schemas']['SimulationLogRecord']

    const restored = restoreSimulationTrace([record], 'session:one', 'north-star')

    expect(restored.selectedStep).toBe(0)
    expect(restored.steps[0].status).toBe('completed')
    expect(restored.steps[0].checkpointId).toBe(record.checkpoint_id)
    expect(restored.steps[0].stages.commit?.summary_text).toBe('Committed')

    const failedRecord = {
      ...record,
      checkpoint_id: null,
      result: { ...record.result, status: 'failed' },
      trace: {
        ...record.trace,
        trace_id: 'trace:failed-attempt',
        status: 'failed',
      },
    } as components['schemas']['SimulationLogRecord']
    const failed = restoreSimulationTrace(
      [failedRecord],
      'session:one',
      'north-star',
      'failed',
      0
    )
    const retried = restoreSimulationTrace(
      [failedRecord],
      'session:one',
      'north-star',
      'paused',
      0
    )

    expect(failed.steps[0].status).toBe('failed')
    expect(retried.steps).toEqual({})
  })
})
