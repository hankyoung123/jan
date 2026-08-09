import type { EngineEventEnvelope, SimulationStage } from '@story-engine/contracts'
import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import {
  initialSimulationViewState,
  reduceSimulationEvent,
  type SimulationViewState,
} from '../evolution/simulationViewModel'
import { AgentConsole } from './AgentConsole'

function event(
  sequence: number,
  type: EngineEventEnvelope['type'],
  payload: Record<string, unknown>
): EngineEventEnvelope {
  return {
    event_id: `event:${sequence}`,
    project_id: 'last-ferry-before',
    subject_id: 'session:one',
    timestamp: `2026-08-09T00:00:${String(sequence).padStart(2, '0')}Z`,
    sequence,
    type,
    payload,
  }
}

function stagePayload(
  step: number,
  stage: SimulationStage,
  status: 'running' | 'succeeded' | 'failed',
  executionId: string,
  actorId: string | null = null
) {
  return {
    event_id: executionId,
    project_id: 'last-ferry-before',
    session_id: 'session:one',
    branch_id: 'main',
    step,
    stage,
    status,
    actor_id: actorId,
    action_spec: null,
    summary_text: stage === 'resolution' && status === 'succeeded' ? '门锁已经被打开。' : null,
    input_record_ids: [],
    output_record_ids: [],
    visible_to: [],
    profile_ids: [],
    model_refs: [],
    prompt_tokens: 0,
    completion_tokens: 0,
    duration_ms: status === 'running' ? null : 125,
    checkpoint_id: null,
    error_code: status === 'failed' ? 'provider_error' : null,
    started_at: '2026-08-09T00:00:00Z',
    completed_at: status === 'running' ? null : '2026-08-09T00:00:00.125Z',
  }
}

function metadata(
  callId: string,
  step: number,
  stage: SimulationStage,
  stageEventId: string,
  agentName = '张野',
  agentType = 'actor'
) {
  return {
    call_id: callId,
    agent_type: agentType,
    agent_name: agentName,
    task_label: stage,
    session_id: 'session:one',
    branch_id: 'main',
    step,
    stage,
    stage_event_id: stageEventId,
    model: 'openai/gpt-test',
    duration_ms: 125,
    prompt_tokens: 12,
    completion_tokens: 7,
    reasoning_tokens: 3,
    retry_count: 1,
  }
}

function renderConsole(state: SimulationViewState) {
  return render(
    <AgentConsole collapsed={false} onCollapsedChange={() => undefined} viewState={state} />
  )
}

function startedState(
  step = 1,
  stage: SimulationStage = 'actor_action',
  executionId = `stage:${step}:${stage}`,
  actorId = 'zhang-ye'
) {
  return reduceSimulationEvent(
    initialSimulationViewState,
    event(1, 'simulation.stage.started', stagePayload(step, stage, 'running', executionId, actorId))
  )
}

describe('AgentConsole', () => {
  it('shows a stage as running as soon as stage.started arrives', () => {
    renderConsole(startedState())

    expect(screen.getByText('Step 1')).toBeInTheDocument()
    expect(screen.getByText('Actor Action')).toBeInTheDocument()
    expect(screen.getAllByText('running').length).toBeGreaterThan(0)
  })

  it('appends model deltas to the live output', () => {
    const executionId = 'stage:delta'
    let state = startedState(1, 'actor_action', executionId)
    const callMetadata = metadata('call:delta', 1, 'actor_action', executionId)
    state = reduceSimulationEvent(state, event(2, 'model.message.started', {
      message_id: 'call:delta', role: 'assistant', metadata: callMetadata,
      input_messages: [{ role: 'user', content: '打开门。' }],
    }))
    state = reduceSimulationEvent(state, event(3, 'model.message.delta', {
      message_id: 'call:delta', role: 'assistant', metadata: callMetadata,
      part: { type: 'text', text_delta: '张野走向' },
    }))
    state = reduceSimulationEvent(state, event(4, 'model.message.delta', {
      message_id: 'call:delta', role: 'assistant', metadata: callMetadata,
      part: { type: 'text', text_delta: '那扇门。' },
    }))
    renderConsole(state)

    expect(screen.getByText('张野走向那扇门。')).toBeInTheDocument()
    expect(screen.getByText('打开门。')).toBeInTheDocument()
  })

  it('shows succeeded and final model metrics after completion', () => {
    const executionId = 'stage:completed'
    let state = startedState(1, 'actor_action', executionId)
    const callMetadata = metadata('call:completed', 1, 'actor_action', executionId)
    state = reduceSimulationEvent(state, event(2, 'model.message.completed', {
      message_id: 'call:completed', role: 'assistant', metadata: callMetadata,
      parts: [{ type: 'text', text: '张野打开了门。' }],
    }))
    state = reduceSimulationEvent(state, event(3, 'simulation.stage.completed', {
      ...stagePayload(1, 'actor_action', 'succeeded', 'terminal:completed', 'zhang-ye'),
    }))
    renderConsole(state)

    expect(screen.getByText('succeeded')).toBeInTheDocument()
    expect(screen.getByText('张野打开了门。')).toBeInTheDocument()
    expect(screen.getAllByText('openai/gpt-test').length).toBeGreaterThan(0)
    expect(screen.getByText('1', { selector: 'dd' })).toBeInTheDocument()
  })

  it('shows stage and model errors when a call fails', () => {
    const executionId = 'stage:failed'
    let state = startedState(1, 'resolution', executionId, 'gm')
    const callMetadata = metadata('call:failed', 1, 'resolution', executionId, 'Game Master', 'game_master')
    state = reduceSimulationEvent(state, event(2, 'model.message.failed', {
      message_id: 'call:failed', role: 'assistant', metadata: callMetadata,
      error: 'provider unavailable', parts: [],
    }))
    state = reduceSimulationEvent(state, event(3, 'simulation.stage.failed', {
      ...stagePayload(1, 'resolution', 'failed', 'terminal:failed', 'gm'),
    }))
    renderConsole(state)

    expect(screen.getAllByText('failed').length).toBeGreaterThan(0)
    expect(screen.getAllByText('provider unavailable').length).toBeGreaterThan(0)
  })

  it('keeps consecutive agents attached to their own stages', () => {
    const gmStage = 'stage:gm'
    const actorStage = 'stage:actor'
    let state = reduceSimulationEvent(
      initialSimulationViewState,
      event(1, 'simulation.stage.started', stagePayload(1, 'action_spec', 'running', gmStage, 'gm'))
    )
    state = reduceSimulationEvent(state, event(2, 'model.message.completed', {
      message_id: 'call:gm', role: 'assistant',
      metadata: metadata('call:gm', 1, 'action_spec', gmStage, 'Game Master', 'game_master'),
      parts: [{ type: 'text', text: 'GM OUTPUT' }],
    }))
    state = reduceSimulationEvent(state, event(3, 'simulation.stage.started', stagePayload(1, 'actor_action', 'running', actorStage, 'zhang-ye')))
    state = reduceSimulationEvent(state, event(4, 'model.message.completed', {
      message_id: 'call:actor', role: 'assistant',
      metadata: metadata('call:actor', 1, 'actor_action', actorStage, '张野'),
      parts: [{ type: 'text', text: 'ACTOR OUTPUT' }],
    }))
    renderConsole(state)

    expect(screen.getByText('GM OUTPUT')).toBeInTheDocument()
    expect(screen.getByText('ACTOR OUTPUT')).toBeInTheDocument()
    expect(state.steps[1].stages.action_spec?.messageIds).toEqual(['call:gm'])
    expect(state.steps[1].stages.actor_action?.messageIds).toEqual(['call:actor'])
  })

  it('groups events from different turns under their own steps', () => {
    let state = startedState(1)
    state = reduceSimulationEvent(state, event(2, 'simulation.stage.started', stagePayload(2, 'resolution', 'running', 'stage:step-2', 'gm')))
    renderConsole(state)

    expect(screen.getByText('Step 1')).toBeInTheDocument()
    expect(screen.getByText('Step 2')).toBeInTheDocument()
  })

  it('ignores a replayed WebSocket sequence instead of duplicating output', () => {
    const executionId = 'stage:reconnect'
    let state = startedState(1, 'actor_action', executionId)
    const delta = event(2, 'model.message.delta', {
      message_id: 'call:reconnect', role: 'assistant',
      metadata: metadata('call:reconnect', 1, 'actor_action', executionId),
      part: { type: 'text', text_delta: 'only once' },
    })
    state = reduceSimulationEvent(state, delta)
    const afterReplay = reduceSimulationEvent(state, delta)
    renderConsole(afterReplay)

    expect(afterReplay).toBe(state)
    expect(screen.getAllByText('only once')).toHaveLength(1)
  })
})
