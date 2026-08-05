import { act, renderHook, waitFor } from '@testing-library/react'
import type { EngineEventEnvelope } from '@story-engine/contracts'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  engineRequest: vi.fn(),
  subscribeProjectEvents: vi.fn(),
}))

vi.mock('./engine', () => ({
  engineRequest: h.engineRequest,
  subscribeProjectEvents: h.subscribeProjectEvents,
}))

import { useProjectModelMessages } from './useProjectModelMessages'

function event(
  sequence: number,
  type: EngineEventEnvelope['type'],
  payload: Record<string, unknown>
): EngineEventEnvelope {
  return {
    event_id: String(sequence).padStart(26, '0'),
    project_id: 'fog-harbor',
    subject_id: 'call:wiki-1',
    timestamp: `2026-08-06T00:00:0${sequence}Z`,
    sequence,
    type,
    payload,
  } as EngineEventEnvelope
}

const metadata = {
  call_id: 'call:wiki-1',
  agent_type: 'wiki_maintainer',
  agent_name: 'Wiki Maintainer',
  task_label: '世界整理',
  session_id: null,
  branch_id: 'main',
  step: 3,
  stage: 'wiki',
  model: 'deepseek/deepseek-chat',
  duration_ms: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
}

describe('useProjectModelMessages', () => {
  beforeEach(() => {
    h.engineRequest.mockReset()
    h.subscribeProjectEvents.mockReset()
  })

  it('replays canonical parts and continues the same message from the stream', async () => {
    let listener: ((value: EngineEventEnvelope) => void) | undefined
    const unsubscribe = vi.fn()
    h.engineRequest.mockResolvedValue([
      event(1, 'model.message.started', {
        message_id: 'call:wiki-1',
        role: 'assistant',
        metadata,
        part: null,
        error: null,
        reset: true,
      }),
      event(2, 'model.message.delta', {
        message_id: 'call:wiki-1',
        role: 'assistant',
        metadata,
        part: { type: 'text', text_delta: '世界' },
        error: null,
        reset: false,
      }),
    ])
    h.subscribeProjectEvents.mockImplementation(
      async (
        _projectId: string,
        callback: (value: EngineEventEnvelope) => void,
        afterSequence: number
      ) => {
        expect(afterSequence).toBe(2)
        listener = callback
        return unsubscribe
      }
    )

    const view = renderHook(() => useProjectModelMessages('fog-harbor'))

    await waitFor(() =>
      expect(view.result.current.messages[0]?.parts).toEqual([
        { type: 'text', text: '世界' },
      ])
    )
    act(() => {
      listener?.(
        event(3, 'model.message.delta', {
          message_id: 'call:wiki-1',
          role: 'assistant',
          metadata,
          part: { type: 'text', text_delta: '已整理' },
          error: null,
          reset: false,
        })
      )
      listener?.(
        event(4, 'model.message.completed', {
          message_id: 'call:wiki-1',
          role: 'assistant',
          metadata: { ...metadata, completion_tokens: 8 },
          part: null,
          error: null,
          reset: false,
        })
      )
    })

    expect(view.result.current.messages[0]?.parts).toEqual([
      { type: 'text', text: '世界已整理' },
    ])
    expect(view.result.current.messages[0]?.metadata?.outputStatus).toBe(
      'completed'
    )
    expect(view.result.current.messages[0]?.metadata?.completionTokens).toBe(8)

    view.unmount()
    expect(unsubscribe).toHaveBeenCalledOnce()
  })
})
