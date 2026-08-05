import type { components, EngineEventEnvelope } from '@story-engine/contracts'
import { describe, expect, it } from 'vitest'

import {
  initialSimulationViewState,
  reduceSimulationEvent,
  restoreSimulationTrace,
  selectViewLocation,
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
  model_refs: ['openai/replay-model'],
  prompt_tokens: 10,
  completion_tokens: 2,
  duration_ms: 45,
  checkpoint_id: null,
  error_code: null,
  started_at: '2026-08-02T00:00:00Z',
  completed_at: '2026-08-02T00:00:00.045Z',
}

describe('simulationViewModel', () => {
  it('merges unified model message part deltas in order', () => {
    const metadata = {
      call_id: 'call:one',
      agent_type: 'actor',
      agent_name: '智秀',
      task_label: '角色行动',
      session_id: 'session:one',
      branch_id: 'main',
      step: 1,
      stage: 'actor_action',
      model: 'deepseek/deepseek-reasoner',
      duration_ms: 1200,
      prompt_tokens: 10,
      completion_tokens: 20,
    }
    let state = reduceSimulationEvent(
      initialSimulationViewState,
      event(1, 'simulation.stage.started', {
        ...stagePayload,
        step: 1,
        stage: 'actor_action',
      })
    )
    state = reduceSimulationEvent(
      state,
      event(2, 'model.message.started', {
        message_id: 'call:one',
        role: 'assistant',
        metadata,
      })
    )
    state = reduceSimulationEvent(
      state,
      event(3, 'model.message.delta', {
        message_id: 'call:one',
        role: 'assistant',
        metadata,
        part: { type: 'reasoning', text_delta: '先判断' },
      })
    )
    state = reduceSimulationEvent(
      state,
      event(4, 'model.message.delta', {
        message_id: 'call:one',
        role: 'assistant',
        metadata,
        part: { type: 'reasoning', text_delta: '角色知识。' },
      })
    )
    state = reduceSimulationEvent(
      state,
      event(5, 'model.message.delta', {
        message_id: 'call:one',
        role: 'assistant',
        metadata,
        part: { type: 'text', text_delta: '智秀推开了门。' },
      })
    )
    state = reduceSimulationEvent(
      state,
      event(6, 'model.message.completed', {
        message_id: 'call:one',
        role: 'assistant',
        metadata,
      })
    )

    expect(state.messages['call:one']).toMatchObject({
      parts: [
        { type: 'reasoning', text: '先判断角色知识。' },
        { type: 'text', text: '智秀推开了门。' },
      ],
      metadata: {
        agentName: '智秀',
        outputStatus: 'completed',
        model: 'deepseek/deepseek-reasoner',
      },
    })
    expect(state.steps[1].stages.actor_action?.messageIds).toEqual([
      'call:one',
    ])
  })

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
      schema_version: 2,
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
            stage_id: 'stage:actor-action',
            stage_type: 'actor_action',
            started_at: '2026-08-02T00:00:00Z',
            completed_at: '2026-08-02T00:00:00.009Z',
            status: 'succeeded',
            actor_id: 'ara',
            model_call_ids: ['call:actor-action'],
            input_record_ids: [],
            output_record_ids: [],
            visible_to: ['ara'],
          },
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
        model_calls: [
          {
            call_id: 'call:actor-action',
            task_id: 'task:actor-action',
            task_type: 'actor',
            status: 'succeeded',
            session_id: 'session:one',
            branch_id: 'main',
            step: 0,
            actor_id: 'ara',
            profile_id: 'actor',
            model_ref: 'deepseek/deepseek-reasoner',
            prompt_version: 'v1',
            content_locale: 'en-US',
            component_ids: ['actor_action'],
            source_record_ids: [],
            message_parts: [
              { type: 'reasoning', text: 'Check what Ara knows.' },
              { type: 'text', text: 'Ara inspects the antenna.' },
            ],
            prompt_sha256: 'b'.repeat(64),
            prompt_tokens: 11,
            completion_tokens: 13,
            reasoning_tokens: 5,
            finish_reason: 'stop',
            max_tokens: 2048,
            reasoning_effort: 'medium',
            temperature: 0.4,
            timeout_seconds: 60,
            duration_ms: 900,
            retry_count: 0,
            error_code: null,
            validation_errors: [],
            started_at: '2026-08-02T00:00:00Z',
            completed_at: '2026-08-02T00:00:00.009Z',
          },
        ],
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
    expect(restored.steps[0].stages.actor_action?.messageIds).toEqual([
      'call:actor-action',
    ])
    expect(restored.messages['call:actor-action']).toMatchObject({
      parts: [
        { type: 'reasoning', text: 'Check what Ara knows.' },
        { type: 'text', text: 'Ara inspects the antenna.' },
      ],
      metadata: {
        agentName: 'ara',
        model: 'deepseek/deepseek-reasoner',
        duration: 0.9,
        outputStatus: 'completed',
      },
    })

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

  it('selects URL step and stage and falls back when stale', () => {
    const first = reduceSimulationEvent(
      initialSimulationViewState,
      event(1, 'simulation.stage.completed', {
        ...stagePayload,
        step: 2,
        stage: 'actor_selection',
      })
    )
    const state = reduceSimulationEvent(
      first,
      event(2, 'simulation.stage.completed', {
        ...stagePayload,
        step: 5,
        stage: 'resolution',
      })
    )

    const selected = selectViewLocation(state, {
      step: 5,
      stage: 'resolution',
    })
    expect(selected.selectedStep).toBe(5)
    expect(selected.selectedStage).toBe('resolution')

    const stale = selectViewLocation(state, {
      step: 99,
      stage: 'actor_selection',
    })
    expect(stale.selectedStep).toBe(5)
    expect(stale.selectedStage).toBe('resolution')

    const missingStage = selectViewLocation(state, {
      step: 2,
      stage: 'commit',
    })
    expect(missingStage.selectedStep).toBe(2)
    expect(missingStage.selectedStage).toBe('actor_selection')
  })
})
