import type { EngineEventEnvelope } from '@story-engine/contracts'
import { describe, expect, it } from 'vitest'

import { reduceModelMessages, type StoryModelMessageMap } from './modelMessages'

const metadata = {
  call_id: 'call:one',
  agent_type: 'actor',
  agent_name: '智秀',
  task_label: '角色行动',
  session_id: 'session:one',
  branch_id: 'main',
  step: 3,
  stage: 'actor_action',
  model: 'deepseek/deepseek-reasoner',
  duration_ms: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
}

function event(
  sequence: number,
  type: EngineEventEnvelope['type'],
  payload: Record<string, unknown>
): EngineEventEnvelope {
  return {
    event_id: `01J0000000000000000000000${sequence}`.slice(-26),
    project_id: 'north-star',
    subject_id: 'session:one',
    timestamp: `2026-08-06T00:00:0${sequence}Z`,
    sequence,
    type,
    payload,
  }
}

function messageEventPayload(overrides: Record<string, unknown> = {}) {
  return {
    message_id: 'call:one',
    role: 'assistant',
    metadata,
    ...overrides,
  }
}

describe('modelMessages', () => {
  it('merges reasoning, text, file, and tool deltas into ordered UIMessage parts', () => {
    const events = [
      event(1, 'model.message.started', messageEventPayload({ reset: true })),
      event(
        2,
        'model.message.delta',
        messageEventPayload({
          part: { type: 'reasoning', text_delta: '先检查知识边界。' },
        })
      ),
      event(
        3,
        'model.message.delta',
        messageEventPayload({
          part: {
            type: 'tool-wiki-update',
            state: 'input-streaming',
            tool_call_id: 'tool:one',
            input: { page: 'world/current.md' },
          },
        })
      ),
      event(
        4,
        'model.message.delta',
        messageEventPayload({
          part: {
            type: 'tool-wiki-update',
            state: 'output-available',
            tool_call_id: 'tool:one',
            output: { updated: true },
          },
        })
      ),
      event(
        5,
        'model.message.delta',
        messageEventPayload({
          part: {
            type: 'file',
            media_type: 'image/png',
            url: 'data:image/png;base64,AA==',
            filename: 'reference.png',
          },
        })
      ),
      event(
        6,
        'model.message.delta',
        messageEventPayload({
          part: { type: 'text', text_delta: '智秀推开了门。' },
        })
      ),
    ]

    const state = events.reduce<StoryModelMessageMap>(reduceModelMessages, {})

    expect(state['call:one'].parts).toEqual([
      { type: 'reasoning', text: '先检查知识边界。' },
      {
        type: 'tool-wiki-update',
        state: 'output-available',
        toolCallId: 'tool:one',
        input: { page: 'world/current.md' },
        output: { updated: true },
      },
      {
        type: 'file',
        mediaType: 'image/png',
        url: 'data:image/png;base64,AA==',
        filename: 'reference.png',
      },
      { type: 'text', text: '智秀推开了门。' },
    ])
  })

  it('resets a retried attempt and preserves start time through completion', () => {
    let state: StoryModelMessageMap = {}
    state = reduceModelMessages(
      state,
      event(1, 'model.message.started', messageEventPayload({ reset: true }))
    )
    state = reduceModelMessages(
      state,
      event(
        2,
        'model.message.delta',
        messageEventPayload({ part: { type: 'text', text_delta: 'discard' } })
      )
    )
    state = reduceModelMessages(
      state,
      event(3, 'model.message.started', messageEventPayload({ reset: true }))
    )
    state = reduceModelMessages(
      state,
      event(
        4,
        'model.message.delta',
        messageEventPayload({ part: { type: 'text', text_delta: 'keep' } })
      )
    )
    state = reduceModelMessages(
      state,
      event(
        5,
        'model.message.completed',
        messageEventPayload({
          metadata: {
            ...metadata,
            duration_ms: 1500,
            prompt_tokens: 7,
            completion_tokens: 9,
          },
        })
      )
    )

    expect(state['call:one']).toMatchObject({
      parts: [{ type: 'text', text: 'keep' }],
      metadata: {
        createdAt: '2026-08-06T00:00:01Z',
        duration: 1.5,
        promptTokens: 7,
        completionTokens: 9,
        outputStatus: 'completed',
      },
    })
  })
})
