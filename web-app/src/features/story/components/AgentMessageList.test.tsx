import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import type { StoryModelMessage } from '../modelMessages'
import { AgentMessageList } from './AgentMessageList'

vi.mock('@/containers/MessageItem', () => ({
  MessageItem: ({ preset }: { preset: string }) => (
    <div data-testid="message-preset">{preset}</div>
  ),
}))

const message: StoryModelMessage = {
  id: 'call:one',
  role: 'assistant',
  parts: [{ type: 'text', text: 'Output' }],
  metadata: {
    callId: 'call:one',
    agentType: 'writer',
    agentName: 'Writer',
    taskLabel: '正文生成',
    model: 'deepseek/deepseek-reasoner',
    duration: 1.25,
    completionTokens: 42,
    outputStatus: 'completed',
  },
}

describe('AgentMessageList', () => {
  it('uses the live-agent MessageItem preset for current Agent activity', () => {
    render(<AgentMessageList messages={[message]} />)

    expect(screen.getByTestId('message-preset')).toHaveTextContent('live-agent')
    expect(screen.getByText('Writer')).toBeInTheDocument()
    expect(
      screen.getByText('deepseek/deepseek-reasoner · 1.3s · 42 tokens')
    ).toBeInTheDocument()
  })

  it('allows history and review surfaces to select readonly explicitly', () => {
    render(<AgentMessageList messages={[message]} preset="readonly" />)

    expect(screen.getByTestId('message-preset')).toHaveTextContent('readonly')
  })
})
