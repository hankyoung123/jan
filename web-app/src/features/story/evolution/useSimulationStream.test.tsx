import { act, renderHook, waitFor } from '@testing-library/react'
import { StrictMode, type ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  engineRequest: vi.fn(),
  subscribeProjectEvents: vi.fn(),
}))

vi.mock('../engine', () => ({
  engineRequest: h.engineRequest,
  subscribeProjectEvents: h.subscribeProjectEvents,
}))

import { useSimulationStream } from './useSimulationStream'

describe('useSimulationStream', () => {
  beforeEach(() => {
    h.engineRequest.mockReset()
    h.subscribeProjectEvents.mockReset()
  })

  it('does not reapply a stale deep-linked step after session progress', async () => {
    h.engineRequest.mockImplementation((path: string) => {
      if (path.includes('/simulation-trace')) return Promise.resolve([])
      if (path.includes('/simulation-events')) {
        return Promise.resolve([
          {
            event_id: '01J00000000000000000000001',
            project_id: 'north-star',
            subject_id: 'session:one',
            timestamp: '2026-08-02T00:00:00Z',
            sequence: 1,
            type: 'simulation.step.completed',
            payload: {
              step: 0,
              status: 'paused',
              acting_actor_id: 'ara',
            },
          },
          {
            event_id: '01J00000000000000000000002',
            project_id: 'north-star',
            subject_id: 'session:one',
            timestamp: '2026-08-02T00:00:01Z',
            sequence: 2,
            type: 'simulation.step.completed',
            payload: {
              step: 1,
              status: 'paused',
              acting_actor_id: 'ara',
            },
          },
        ])
      }
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    h.subscribeProjectEvents.mockResolvedValue(() => undefined)
    const initialLocation = { step: 0, stage: 'resolution' as const }

    const { result, rerender } = renderHook(
      ({ currentStep }: { currentStep: number }) =>
        useSimulationStream({
          projectId: 'north-star',
          sessionId: 'session:one',
          branchId: 'main',
          currentStep,
          initialLocation,
        }),
      {
        initialProps: { currentStep: 0 },
        wrapper: ({ children }: { children: ReactNode }) => (
          <StrictMode>{children}</StrictMode>
        ),
      }
    )

    await waitFor(() => {
      expect(result.current.viewState.steps[0]?.status).toBe('completed')
      expect(result.current.viewState.selectedStep).toBe(0)
    })
    rerender({ currentStep: 1 })
    await waitFor(() => expect(result.current.viewState.selectedStep).toBe(1))
  })

  it('reloads durable trace after resync and then continues with live events', async () => {
    const callbacks: Array<(event: {
      event_id: string
      project_id: string
      subject_id: string
      timestamp: string
      sequence: number
      type: 'stream.resync_required' | 'simulation.stage.started'
      payload: Record<string, unknown>
    }) => void> = []
    let traceReads = 0
    const durableRecord = {
      schema_version: 2,
      checkpoint_id: 'checkpoint:one',
      state_hash: 'a'.repeat(64),
      result: {
        session_id: 'session:one', branch_id: 'main', step: 1,
        acting_actor_id: 'ara', action_spec: null, action_text: 'wait',
        resolved_turn: null, status: 'paused', checkpoint_id: null, boundary: 'none',
      },
      trace: {
        trace_id: 'trace:one', session_id: 'session:one', branch_id: 'main', step: 1,
        content_locale: 'zh-CN', model_calls: [], action_spec: null,
        acting_actor_id: 'ara', putative_event_record_id: null,
        resolved_event_record_ids: [], started_at: '2026-08-09T00:00:00Z',
        completed_at: '2026-08-09T00:00:01Z', status: 'succeeded',
        stages: [{
          stage_id: 'stage:durable', stage_type: 'commit',
          started_at: '2026-08-09T00:00:00Z', completed_at: '2026-08-09T00:00:01Z',
          status: 'succeeded', actor_id: 'ara', action_spec: null,
          model_call_ids: [], input_record_ids: [], output_record_ids: [],
          visible_to: [], prompt_tokens: 0, completion_tokens: 0,
          duration_ms: 1000, checkpoint_id: 'checkpoint:one', error_code: null,
          detail_text: 'Committed',
        }],
      },
    }
    h.engineRequest.mockImplementation((path: string) => {
      if (path.includes('/simulation-trace')) {
        traceReads += 1
        return Promise.resolve(traceReads === 1 ? [] : [durableRecord])
      }
      if (path.includes('/simulation-events')) return Promise.resolve([])
      return Promise.reject(new Error(`Unexpected request: ${path}`))
    })
    h.subscribeProjectEvents.mockImplementation((
      _projectId: string,
      callback: (typeof callbacks)[number]
    ) => {
      callbacks.push(callback)
      return Promise.resolve(() => undefined)
    })

    const { result } = renderHook(() => useSimulationStream({
      projectId: 'north-star',
      sessionId: 'session:one',
      branchId: 'main',
      currentStep: 2,
    }))
    await waitFor(() => expect(callbacks).toHaveLength(1))

    act(() => callbacks[0]({
      event_id: 'resync:1', project_id: 'north-star', subject_id: 'stream',
      timestamp: '2026-08-09T00:00:02Z', sequence: 5,
      type: 'stream.resync_required', payload: { reason: 'sequence_gap' },
    }))
    await waitFor(() => {
      expect(traceReads).toBe(2)
      expect(result.current.viewState.steps[1]?.stages.commit?.summary_text).toBe('Committed')
      expect(callbacks).toHaveLength(2)
    })

    act(() => callbacks[1]({
      event_id: 'stage:live', project_id: 'north-star', subject_id: 'session:one',
      timestamp: '2026-08-09T00:00:03Z', sequence: 6,
      type: 'simulation.stage.started',
      payload: {
        event_id: 'stage:live', project_id: 'north-star', session_id: 'session:one',
        branch_id: 'main', step: 2, stage: 'resolution', status: 'running',
        actor_id: null, action_spec: null, summary_text: null,
        input_record_ids: [], output_record_ids: [], visible_to: [], profile_ids: [],
        model_refs: [], prompt_tokens: 0, completion_tokens: 0, duration_ms: null,
        checkpoint_id: null, error_code: null, started_at: '2026-08-09T00:00:03Z',
        completed_at: null,
      },
    }))

    await waitFor(() => expect(result.current.viewState.steps[2]?.stages.resolution?.status).toBe('running'))
  })
})
