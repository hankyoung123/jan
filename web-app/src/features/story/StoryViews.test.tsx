import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({ engineRequest: vi.fn() }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))

vi.mock('./engine', () => ({ engineRequest: h.engineRequest }))

import { EvolutionView, SubmissionView } from './StoryViews'

const candidate = {
  id: 'turn-01',
  project_id: 'fog-harbor',
  base_world_version: 12,
  base_character_versions: { 'chen-mo': 3, 'lin-lan': 2 },
  intents: [
    {
      character_id: 'chen-mo',
      action: '检查灯芯槽',
      target: '灯塔照明装置',
      goal: '判断灯塔是否被人为关闭',
      knowledge_basis: ['fact:lighthouse-never-off'],
      recognized_risk: '可能暴露调查',
    },
    {
      character_id: 'lin-lan',
      action: '启动备用航标',
      target: '近港航道',
      goal: '保证客船安全',
      knowledge_basis: ['fact:beacon-ready'],
      recognized_risk: '电量不足',
    },
  ],
  outcome: {
    summary: '陈默在灯芯槽中发现了新鲜刮痕。',
    public_results: ['灯塔暂未恢复'],
    hidden_results: [],
    character_changes: [],
    world_changes: [],
    new_npcs: [],
    unresolved_consequences: [],
  },
  review: {
    mode: 'turn_review',
    passed: true,
    summary: '未发现知识越界或世界规则冲突',
    issues: [],
  },
  status: 'reviewed',
}

describe('Story submission', () => {
  beforeEach(() => h.engineRequest.mockReset())

  it('creates a runnable initial world through the Python API', async () => {
    h.engineRequest.mockResolvedValue({ world: { version: 1 } })
    render(<SubmissionView />)

    fireEvent.click(screen.getByRole('button', { name: '创建雾港项目' }))

    await waitFor(() => expect(h.engineRequest).toHaveBeenCalledOnce())
    const [path, init] = h.engineRequest.mock.calls[0]
    const payload = JSON.parse(String(init.body)) as Record<string, unknown>
    expect(path).toBe('/submissions/finalize')
    expect(payload.characters).toHaveLength(2)
    expect(payload).not.toHaveProperty('outline')
    expect(await screen.findByRole('status')).toHaveTextContent('世界版本 1')
  })
})

describe('Story evolution', () => {
  beforeEach(() => h.engineRequest.mockReset())

  it('generates isolated intents and exposes only the V1 decisions', async () => {
    h.engineRequest.mockResolvedValue(candidate)
    render(<EvolutionView />)

    fireEvent.click(screen.getByRole('button', { name: '生成角色行动' }))

    expect(await screen.findByText(candidate.outcome.summary)).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/fog-harbor/turns/generate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ participant_ids: ['chen-mo', 'lin-lan'] }),
      })
    )
    expect(screen.getByRole('button', { name: '要求修改' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '放弃本轮' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '确认本轮' })).toBeEnabled()
  })

  it('confirms through the commit endpoint before reporting Markdown changes', async () => {
    h.engineRequest
      .mockResolvedValueOnce(candidate)
      .mockResolvedValueOnce({
        candidate: { ...candidate, status: 'committed' },
        event: { id: 'event-000013' },
      })
    render(<EvolutionView />)

    fireEvent.click(screen.getByRole('button', { name: '生成角色行动' }))
    await screen.findByText(candidate.outcome.summary)
    fireEvent.click(screen.getByRole('button', { name: '确认本轮' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      'event-000013 已写入正式 Markdown'
    )
    expect(h.engineRequest).toHaveBeenLastCalledWith(
      '/projects/fog-harbor/turns/turn-01/confirm',
      { method: 'POST' }
    )
  })
})
