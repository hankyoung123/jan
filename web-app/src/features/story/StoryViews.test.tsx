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

import {
  clearActiveStoryProject,
  getActiveStoryProjectId,
  setActiveStoryProjectId,
} from './activeProject'
import { EvolutionView, SubmissionView } from './StoryViews'

const projectSnapshot = {
  project: {
    schema: 'project/v1',
    id: 'north-star',
    title: '北辰',
    genre: '科幻',
    theme: '记忆与身份',
    tone: '冷静、辽阔',
    version: 0,
  },
  world: {
    current_time: '极夜第三日',
    current_location: '北境观测站',
    rules: ['观测站与外界失联'],
    active_pressures: ['备用氧气只剩六小时'],
    public_fact_ids: ['fact:station-offline'],
    world_variables: { initial_incident: '主天线在极光中失效' },
    version: 4,
  },
  characters: [
    {
      id: 'ara',
      display_name: '阿岚',
      type: 'active',
      identity: '通信工程师',
      core_desire: '找到失联原因',
      current_goal: '修复主天线',
      known_fact_ids: ['secret:ara-signal'],
      relationships: [],
      location: '天线塔',
      emotional_state: '专注',
      resources: ['频谱仪'],
      last_event_id: null,
      version: 2,
    },
    {
      id: 'bo',
      display_name: '柏舟',
      type: 'active',
      identity: '站务主管',
      core_desire: '让所有人活着离开',
      current_goal: '恢复氧气循环',
      known_fact_ids: ['secret:bo-oxygen'],
      relationships: [],
      location: '生命支持舱',
      emotional_state: '克制',
      resources: ['总控钥匙'],
      last_event_id: null,
      version: 1,
    },
  ],
}

const candidate = {
  id: 'turn-04',
  project_id: 'north-star',
  base_world_version: 4,
  base_character_versions: { ara: 2, bo: 1 },
  intents: [
    {
      character_id: 'ara',
      action: '检查主天线的异常频谱',
      target: '主天线',
      goal: '修复主天线',
      knowledge_basis: ['secret:ara-signal'],
      recognized_risk: '极光可能再次过载',
    },
    {
      character_id: 'bo',
      action: '隔离损坏的氧气循环支路',
      target: '生命支持系统',
      goal: '恢复氧气循环',
      knowledge_basis: ['secret:bo-oxygen'],
      recognized_risk: '隔离会降低其他舱室供氧',
    },
  ],
  outcome: {
    summary: '观测站暂时恢复一条低带宽通信链路。',
    public_results: ['外界回应了一段校验信号'],
    hidden_results: [],
    character_changes: [],
    world_changes: [],
    new_npcs: [],
    unresolved_consequences: ['氧气压力仍在下降'],
  },
  review: {
    mode: 'turn_review',
    passed: true,
    summary: '知识边界、世界规则和状态来源检查通过。',
    issues: [],
  },
  status: 'reviewed',
}

function mockLoadedProject() {
  h.engineRequest.mockImplementation((path: string) => {
    if (path === '/projects/north-star') return Promise.resolve(projectSnapshot)
    if (path === '/projects/north-star/turns/generate') {
      return Promise.resolve(candidate)
    }
    throw new Error(`Unexpected request: ${path}`)
  })
}

describe('Story submission', () => {
  beforeEach(() => {
    h.engineRequest.mockReset()
    clearActiveStoryProject()
  })

  it('creates a runnable initial world and selects it without storing domain state', async () => {
    h.engineRequest.mockResolvedValue({
      ...projectSnapshot,
      project: { ...projectSnapshot.project, id: 'fog-harbor', title: '雾港' },
      world: { ...projectSnapshot.world, version: 1 },
    })
    render(<SubmissionView />)

    fireEvent.click(screen.getByRole('button', { name: '创建雾港项目' }))

    await waitFor(() => expect(h.engineRequest).toHaveBeenCalledOnce())
    const [path, init] = h.engineRequest.mock.calls[0]
    const payload = JSON.parse(String(init.body)) as Record<string, unknown>
    expect(path).toBe('/submissions/finalize')
    expect(payload.characters).toHaveLength(2)
    expect(payload).not.toHaveProperty('outline')
    expect(getActiveStoryProjectId()).toBe('fog-harbor')
    expect(await screen.findByRole('status')).toHaveTextContent('世界版本 1')
    expect(screen.getByRole('link', { name: '进入第一轮' })).toHaveAttribute(
      'href',
      '/evolve'
    )
  })
})

describe('Story evolution', () => {
  beforeEach(() => {
    h.engineRequest.mockReset()
    clearActiveStoryProject()
  })

  it('requires an active story project instead of assuming the fog-harbor fixture', () => {
    render(<EvolutionView />)

    expect(screen.getByText('尚未选择故事项目')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '前往投稿' })).toHaveAttribute(
      'href',
      '/submission'
    )
    expect(h.engineRequest).not.toHaveBeenCalled()
  })

  it('loads canonical project state and derives participants and names from it', async () => {
    setActiveStoryProjectId('north-star')
    mockLoadedProject()
    render(<EvolutionView />)

    expect(await screen.findByText('主天线在极光中失效')).toBeInTheDocument()
    expect(screen.getByText('北境观测站')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '生成角色行动' }))

    expect(await screen.findByText(candidate.outcome.summary)).toBeInTheDocument()
    expect(screen.getByText('阿岚')).toBeInTheDocument()
    expect(screen.getByText('柏舟')).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith('/projects/north-star')
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/north-star/turns/generate',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ participant_ids: ['ara', 'bo'] }),
      })
    )
    expect(screen.getByRole('button', { name: '要求修改' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '放弃本轮' })).toBeEnabled()
    expect(screen.getByRole('button', { name: '确认本轮' })).toBeEnabled()
    expect(
      screen.getByRole('button', { name: '本轮待确认' })
    ).toBeDisabled()
    expect(
      screen.queryByRole('button', { name: '重新生成角色行动' })
    ).not.toBeInTheDocument()
  })

  it('uses only revision, discard, and confirm as active-candidate decisions', async () => {
    setActiveStoryProjectId('north-star')
    const revised = {
      ...candidate,
      outcome: { ...candidate.outcome, summary: '通信链路以更克制的方式恢复。' },
    }
    const discarded = { ...revised, status: 'discarded' }
    h.engineRequest.mockImplementation((path: string) => {
      if (path === '/projects/north-star') return Promise.resolve(projectSnapshot)
      if (path.endsWith('/turns/generate')) return Promise.resolve(candidate)
      if (path.endsWith('/request-revision')) return Promise.resolve(revised)
      if (path.endsWith('/discard')) return Promise.resolve(discarded)
      throw new Error(`Unexpected request: ${path}`)
    })
    render(<EvolutionView />)
    await screen.findByText('主天线在极光中失效')
    fireEvent.click(screen.getByRole('button', { name: '生成角色行动' }))
    await screen.findByText(candidate.outcome.summary)

    fireEvent.change(screen.getByRole('textbox', { name: '修改要求' }), {
      target: { value: '降低结果强度' },
    })
    fireEvent.click(screen.getByRole('button', { name: '要求修改' }))

    expect(await screen.findByText(revised.outcome.summary)).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/north-star/turns/turn-04/request-revision',
      expect.objectContaining({
        body: JSON.stringify({ instruction: '降低结果强度' }),
      })
    )
    fireEvent.click(screen.getByRole('button', { name: '放弃本轮' }))
    expect(await screen.findByRole('status')).toHaveTextContent(
      '本轮已放弃，正式状态未改变'
    )
  })

  it('confirms through the commit endpoint and reloads canonical project state', async () => {
    setActiveStoryProjectId('north-star')
    const committedProject = {
      ...projectSnapshot,
      world: { ...projectSnapshot.world, version: 5 },
    }
    let projectReads = 0
    h.engineRequest.mockImplementation((path: string) => {
      if (path === '/projects/north-star') {
        projectReads += 1
        return Promise.resolve(projectReads === 1 ? projectSnapshot : committedProject)
      }
      if (path.endsWith('/turns/generate')) return Promise.resolve(candidate)
      if (path.endsWith('/confirm')) {
        return Promise.resolve({
          candidate: { ...candidate, status: 'committed' },
          event: { id: 'event-000005' },
        })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    render(<EvolutionView />)
    await screen.findByText('主天线在极光中失效')
    fireEvent.click(screen.getByRole('button', { name: '生成角色行动' }))
    await screen.findByText(candidate.outcome.summary)
    fireEvent.click(screen.getByRole('button', { name: '确认本轮' }))

    expect(await screen.findByRole('status')).toHaveTextContent(
      'event-000005 已写入正式 Markdown'
    )
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/north-star/turns/turn-04/confirm',
      { method: 'POST' }
    )
    await waitFor(() => expect(projectReads).toBe(2))
    expect(screen.getByText('北辰 / 世界版本 5')).toBeInTheDocument()
  })

  it('shows project loading failures as a recoverable page error', async () => {
    setActiveStoryProjectId('north-star')
    h.engineRequest.mockRejectedValue(new Error('项目目录不可读'))
    render(<EvolutionView />)

    expect(await screen.findByRole('alert')).toHaveTextContent('项目目录不可读')
    expect(screen.getByRole('button', { name: '重试加载' })).toBeEnabled()
  })
})
