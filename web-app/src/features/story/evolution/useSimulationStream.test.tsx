import { renderHook, waitFor } from '@testing-library/react'
import { StrictMode, type ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

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
})
