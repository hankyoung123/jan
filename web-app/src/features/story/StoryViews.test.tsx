import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  engineRequest: vi.fn(),
  subscribeProjectEvents: vi.fn(),
}))

vi.stubGlobal(
  'ResizeObserver',
  class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
)

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
  useParams: () => ({}),
}))

vi.mock('./engine', () => ({
  engineRequest: h.engineRequest,
  subscribeProjectEvents: h.subscribeProjectEvents,
}))

vi.mock('@/containers/MessageItem', () => ({
  MessageItem: ({
    message,
  }: {
    message: { parts: Array<{ type: string; text?: string }> }
  }) => (
    <div data-testid="jan-message-item">
      {message.parts
        .filter((part) => part.type === 'text')
        .map((part) => part.text)
        .join('')}
    </div>
  ),
}))

import {
  clearActiveStoryProject,
  getActiveStoryProjectId,
  setActiveStoryProjectId,
} from './activeProject'
import {
  CharactersView,
  EvolutionView,
  SubmissionView,
  WorkbenchView,
} from './StoryViews'
import { resetSubmissionSession } from './submission/session'
import { TranslationProvider } from '@/i18n'

function renderEvolution() {
  return render(
    <TranslationProvider>
      <EvolutionView />
    </TranslationProvider>
  )
}

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
      version: 1,
    },
  ],
  facts: [
    {
      id: 'fact:station-offline',
      statement: '观测站与外界失联。',
      visibility: 'public',
      known_by: [],
      source_event_id: 'submission:north-star',
      introduced_at: '2026-07-31T11:00:00Z',
      supersedes_fact_id: null,
    },
    {
      id: 'secret:ara-signal',
      statement: '阿岚捕获到一段异常校验信号。',
      visibility: 'secret',
      known_by: ['ara'],
      source_event_id: 'submission:north-star',
      introduced_at: '2026-07-31T11:00:00Z',
      supersedes_fact_id: null,
    },
    {
      id: 'secret:bo-oxygen',
      statement: '备用氧气的真实余量低于公开读数。',
      visibility: 'secret',
      known_by: ['bo'],
      source_event_id: 'submission:north-star',
      introduced_at: '2026-07-31T11:00:00Z',
      supersedes_fact_id: null,
    },
  ],
}

const workspaceState = {
  project: projectSnapshot,
  index: {
    project_id: 'north-star',
    revision: 'a'.repeat(64),
    world_version: 4,
    character_versions: { ara: 2, bo: 1 },
    fact_ids: ['fact:station-offline'],
    documents: [
      {
        relative_path: 'project.md',
        kind: 'project',
        document_id: 'north-star',
        version: 0,
        sha256: 'b'.repeat(64),
      },
      {
        relative_path: 'world.md',
        kind: 'world',
        document_id: 'world',
        version: 4,
        sha256: 'c'.repeat(64),
      },
    ],
  },
  recovered_transactions: 0,
  status: 'open',
  last_error: null,
}

describe('Story workspace lifecycle', () => {
  beforeEach(() => {
    clearActiveStoryProject()
    h.engineRequest.mockReset()
    h.subscribeProjectEvents.mockReset()
    h.subscribeProjectEvents.mockResolvedValue(() => undefined)
  })

  it('lists canonical projects and explicitly opens the selected workspace', async () => {
    h.engineRequest.mockImplementation((path: string) => {
      if (path === '/projects') {
        return Promise.resolve([
          {
            id: 'north-star',
            title: '北辰',
            genre: '科幻',
            world_version: 4,
            is_open: false,
          },
        ])
      }
      if (path === '/projects/north-star/open') {
        return Promise.resolve(workspaceState)
      }
      throw new Error(`Unexpected request: ${path}`)
    })

    render(<WorkbenchView />)
    fireEvent.click(await screen.findByRole('button', { name: '打开 北辰' }))

    expect(await screen.findByText('极夜第三日')).toBeInTheDocument()
    expect(getActiveStoryProjectId()).toBe('north-star')
    expect(h.engineRequest).toHaveBeenCalledWith('/projects/north-star/open', {
      method: 'POST',
    })
    expect(h.subscribeProjectEvents).toHaveBeenCalledWith(
      'north-star',
      expect.any(Function)
    )
  })

  it('restores and closes the active workspace without deleting Markdown', async () => {
    setActiveStoryProjectId('north-star')
    h.engineRequest.mockImplementation((path: string) => {
      if (path === '/projects') {
        return Promise.resolve([
          {
            id: 'north-star',
            title: '北辰',
            genre: '科幻',
            world_version: 4,
            is_open: false,
          },
        ])
      }
      if (path === '/projects/north-star/open') {
        return Promise.resolve(workspaceState)
      }
      if (path === '/projects/north-star/close') {
        return Promise.resolve({ project_id: 'north-star', status: 'closed' })
      }
      throw new Error(`Unexpected request: ${path}`)
    })

    render(<WorkbenchView />)
    fireEvent.click(await screen.findByRole('button', { name: '关闭项目' }))

    await waitFor(() => expect(getActiveStoryProjectId()).toBeNull())
    expect(screen.getByText('选择一个项目开始工作')).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith('/projects/north-star/close', {
      method: 'POST',
    })
  })
})

describe('Story submission', () => {
  beforeEach(() => {
    h.engineRequest.mockReset()
    h.subscribeProjectEvents.mockReset()
    h.subscribeProjectEvents.mockResolvedValue(() => undefined)
    clearActiveStoryProject()
    resetSubmissionSession()
  })

  it('keeps unsent submission text across route unmounts', () => {
    const view = render(<SubmissionView />)

    fireEvent.change(screen.getByRole('textbox', { name: '投稿消息' }), {
      target: { value: '保留这段尚未发送的世界设定。' },
    })
    view.unmount()

    render(<SubmissionView />)
    expect(screen.getByRole('textbox', { name: '投稿消息' })).toHaveValue(
      '保留这段尚未发送的世界设定。'
    )
  })

  it('keeps an in-flight submission result after leaving the route', async () => {
    let resolveRequest: ((value: unknown) => void) | undefined
    h.engineRequest.mockReturnValue(
      new Promise((resolve) => {
        resolveRequest = resolve
      })
    )
    const view = render(<SubmissionView />)
    fireEvent.change(screen.getByRole('textbox', { name: '投稿消息' }), {
      target: { value: '建立一座暴雨中的海港。' },
    })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))
    view.unmount()

    await act(async () => {
      resolveRequest?.({
        reply: '海港世界已经记录，可以继续补充角色。',
        draft: {
          id: 'story-pending',
          title: '暴雨港',
          genre: '悬疑',
          theme: '信任',
          tone: '压迫',
          world_rules: [],
          facts: [],
          characters: [],
          initial_time: '',
          initial_location: '',
          initial_incident: '',
          pressures: [],
        },
        review: {
          mode: 'submission_review',
          passed: false,
          summary: '仍需补充初始角色。',
          issues: [],
        },
        runnable: false,
        missing_requirements: ['初始角色 (2-4 个)'],
      })
    })

    render(<SubmissionView />)
    expect(
      screen.getByText('海港世界已经记录，可以继续补充角色。')
    ).toBeInTheDocument()
    expect(screen.getByText('暴雨港')).toBeInTheDocument()
    expect(screen.getByText('初始角色 (2-4 个)')).toBeInTheDocument()
  })

  it('restores the composer when submission discussion fails', async () => {
    h.engineRequest.mockRejectedValue(
      new Error('provider returned HTTP 400: Invalid Format')
    )
    render(<SubmissionView />)

    fireEvent.change(screen.getByRole('textbox', { name: '投稿消息' }), {
      target: { value: '这段内容在失败后仍应可编辑。' },
    })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid Format')
    expect(screen.getByRole('textbox', { name: '投稿消息' })).toHaveValue(
      '这段内容在失败后仍应可编辑。'
    )
    expect(screen.getAllByTestId('jan-message-item')).toHaveLength(1)
  })

  it('discusses a setting package before creating and selecting the project', async () => {
    let draftId = ''
    const completedDraft = {
      id: '',
      title: '北辰',
      genre: '科幻',
      theme: '记忆与身份',
      tone: '冷静、辽阔',
      world_rules: ['观测站与外界失联'],
      public_fact_ids: ['fact:station-offline'],
      characters: projectSnapshot.characters.map((character) => ({
        id: character.id,
        display_name: character.display_name,
        identity: character.identity,
        core_desire: character.core_desire,
        current_goal: character.current_goal,
        known_fact_ids: character.known_fact_ids,
        location: character.location,
        emotional_state: character.emotional_state,
        resources: character.resources,
      })),
      initial_time: projectSnapshot.world.current_time,
      initial_location: projectSnapshot.world.current_location,
      initial_incident: '主天线在极光中失效',
      pressures: projectSnapshot.world.active_pressures,
    }
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path.endsWith('/submission/messages')) {
        const request = JSON.parse(String(init?.body))
        draftId = request.draft.id
        return Promise.resolve({
          reply: '初始世界已经具备运行条件，可以继续调整或创建项目。',
          draft: { ...completedDraft, id: draftId },
          review: {
            mode: 'submission_review',
            passed: false,
            summary: '创作方向、世界压力和角色知识边界明确。',
            issues: [],
          },
          runnable: true,
          missing_requirements: [],
        })
      }
      if (path === '/submissions/finalize') {
        return Promise.resolve({
          ...projectSnapshot,
          project: { ...projectSnapshot.project, id: draftId },
          world: { ...projectSnapshot.world, version: 1 },
        })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    render(<SubmissionView />)

    expect(screen.getByTestId('jan-chat-shell')).toBeInTheDocument()
    expect(screen.getByTestId('chat-input')).toHaveAttribute(
      'placeholder',
      '描述类型、主题、世界规则、人物或起始事件…'
    )
    expect(screen.getAllByTestId('jan-message-item')).toHaveLength(1)
    expect(screen.getByRole('button', { name: '创建项目' })).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: '投稿消息' }), {
      target: { value: '我想写一篇极夜观测站里的科幻故事。' },
    })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))

    expect(
      await screen.findByText(
        '初始世界已经具备运行条件，可以继续调整或创建项目。'
      )
    ).toBeInTheDocument()
    expect(screen.getAllByTestId('jan-message-item')).toHaveLength(3)
    expect(screen.getByText('北辰')).toBeInTheDocument()
    expect(screen.getByText('阿岚')).toBeInTheDocument()
    expect(screen.getByText('柏舟')).toBeInTheDocument()
    expect(screen.getByText('设定包可运行')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建项目' })).toBeEnabled()

    const discussionCall = h.engineRequest.mock.calls[0]
    expect(discussionCall[0]).toMatch(
      /^\/projects\/story-[a-z0-9-]+\/submission\/messages$/
    )
    expect(JSON.parse(String(discussionCall[1].body)).messages).toEqual([
      expect.objectContaining({ role: 'assistant' }),
      {
        role: 'user',
        content: '我想写一篇极夜观测站里的科幻故事。',
      },
    ])

    fireEvent.click(screen.getByRole('button', { name: '创建项目' }))

    await waitFor(() => expect(h.engineRequest).toHaveBeenCalledTimes(2))
    const [path, init] = h.engineRequest.mock.calls[1]
    const payload = JSON.parse(String(init.body)) as Record<string, unknown>
    expect(path).toBe('/submissions/finalize')
    expect(payload.characters).toHaveLength(2)
    expect(payload).not.toHaveProperty('outline')
    expect(getActiveStoryProjectId()).toBe(draftId)
    expect(await screen.findByRole('status')).toHaveTextContent('世界版本 1')
    expect(screen.getByRole('link', { name: '进入第一轮' })).toHaveAttribute(
      'href',
      '/evolve'
    )
  })

  it('shows missing requirements and keeps creation locked', async () => {
    h.engineRequest.mockImplementation((_path: string, init?: RequestInit) => {
      const request = JSON.parse(String(init?.body))
      return Promise.resolve({
        reply: '先确定故事发生的时间、地点和起始事件。',
        draft: {
          ...request.draft,
          title: '无名站',
          genre: '科幻',
          theme: '孤独',
          tone: '冷静',
        },
        review: {
          mode: 'submission_review',
          passed: false,
          summary: '初始设定包尚未达到可运行条件。',
          issues: [],
        },
        runnable: false,
        missing_requirements: ['世界规则与公共事实', '初始角色 (2-4 个)'],
      })
    })
    render(<SubmissionView />)

    fireEvent.change(screen.getByRole('textbox', { name: '投稿消息' }), {
      target: { value: '我想写科幻。' },
    })
    fireEvent.click(screen.getByRole('button', { name: '发送消息' }))

    expect(await screen.findByText('世界规则与公共事实')).toBeInTheDocument()
    expect(screen.getByText('初始角色 (2-4 个)')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '创建项目' })).toBeDisabled()
  })
})

const baseSimulationSession = {
  session_id: 'session:one',
  project_id: 'north-star',
  branch_id: 'main',
  status: 'created',
  pending_control: 'none',
  content_locale: 'zh-CN',
  request: {
    project_id: 'north-star',
    branch_id: 'main',
    premise_text: '主天线在极光中失效',
    actor_ids: [],
    content_locale: 'zh-CN',
    control: {
      mode: 'scene',
      pause_after_scene: true,
      max_steps: 100,
      max_scenes: 12,
      max_total_tokens: 500000,
      max_runtime_seconds: 3600,
      max_consecutive_model_failures: 3,
      allow_user_override: true,
      checkpoint_every_steps: 5,
    },
    output: {
      manuscript_mode: 'manual',
      wiki_mode: 'after_scene',
    },
    seed: null,
  },
  current_step: 0,
  completed_scenes: 0,
  roster_actor_ids: ['ara', 'bo'],
  characters: projectSnapshot.characters,
  pending_scene_events: [],
  actor_states: {},
  game_master_states: {},
  memory_snapshots: {},
  raw_log_offset: 0,
  total_model_tokens: 0,
  consecutive_model_failures: 0,
  checkpoint_id: 'checkpoint-' + 'a'.repeat(64),
  started_at: '2026-08-02T00:00:00Z',
  updated_at: '2026-08-02T00:00:00Z',
  termination_reason_text: null,
  restoration_notice_text: null,
  maintenance_status: 'not_required',
  maintenance_error_text: null,
  maintenance_step: null,
  maintenance_boundary: 'none',
  state_hash: 'a'.repeat(64),
  active_actor_id: null,
  current_action_spec: null,
}

describe('Story simulation', () => {
  beforeEach(() => {
    h.engineRequest.mockReset()
    clearActiveStoryProject()
    window.history.replaceState({}, '', '/')
  })

  it('requires an active story project', () => {
    renderEvolution()

    expect(screen.getByText('No story project selected')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to submission' })).toHaveAttribute(
      'href',
      '/submission'
    )
  })

  it('starts and advances a persistent simulation session', async () => {
    setActiveStoryProjectId('north-star')
    const session = baseSimulationSession
    let currentSession = session
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/projects/north-star') return Promise.resolve(projectSnapshot)
      if (path === '/projects/north-star/branches') {
        return Promise.resolve([
          {
            branch_id: 'main',
            project_id: 'north-star',
            head_checkpoint_id: session.checkpoint_id,
            head_step: 0,
            parent_branch_id: null,
            source_checkpoint_id: null,
            content_locale: 'zh-CN',
            updated_at: '2026-08-02T00:00:00Z',
          },
        ])
      }
      if (path === '/projects/north-star/simulations' && !init) {
        return Promise.resolve([])
      }
      if (path === '/projects/north-star/simulations' && init?.method === 'POST') {
        return Promise.resolve(currentSession)
      }
      if (path === `/projects/north-star/simulations/${session.session_id}`) {
        return Promise.resolve(currentSession)
      }
      if (path.includes('/simulation-events')) return Promise.resolve([])
      if (path.includes('/simulation-trace')) return Promise.resolve([])
      if (path.endsWith('/step')) {
        currentSession = { ...currentSession, current_step: 1, status: 'paused' }
        return Promise.resolve({
          session_id: session.session_id,
          branch_id: 'main',
          step: 0,
          acting_actor_id: 'ara',
          action_spec: null,
          action_text: '检查天线',
          resolved_turn: null,
          status: 'paused',
          checkpoint_id: session.checkpoint_id,
        })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    renderEvolution()

    await screen.findByDisplayValue('主天线在极光中失效')
    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))
    expect(await screen.findByText('Step 0')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Single step' }))
    expect(await screen.findByText('Step 1')).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/north-star/simulations',
      expect.objectContaining({ method: 'POST' })
    )
    const startRequest = h.engineRequest.mock.calls.find(
      ([path, init]) =>
        path === '/projects/north-star/simulations' && init?.method === 'POST'
    )?.[1] as RequestInit
    expect(JSON.parse(startRequest.body as string)).toEqual({
      branch_id: 'main',
      premise_text: '主天线在极光中失效',
      actor_ids: [],
      content_locale: 'zh-CN',
      control: {
        mode: 'scene',
        max_steps: 100,
        max_scenes: 12,
        max_total_tokens: 500000,
        max_runtime_seconds: 3600,
        max_consecutive_model_failures: 3,
        pause_after_scene: true,
        allow_user_override: true,
        checkpoint_every_steps: 5,
      },
      output: {
        manuscript_mode: 'manual',
        wiki_mode: 'after_scene',
      },
    })
  })

  it('shows a failed Wiki maintenance state and retries it', async () => {
    setActiveStoryProjectId('north-star')
    const failedSession = {
      ...baseSimulationSession,
      status: 'paused',
      maintenance_status: 'failed',
      maintenance_error_text: 'Wiki provider timed out',
      maintenance_step: 1,
      maintenance_boundary: 'scene',
    }
    const succeededSession = {
      ...failedSession,
      maintenance_status: 'succeeded',
      maintenance_error_text: null,
    }

    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/projects/north-star') return Promise.resolve(projectSnapshot)
      if (path === '/projects/north-star/branches') return Promise.resolve([])
      if (path === '/projects/north-star/simulations') {
        return Promise.resolve([
          {
            session_id: failedSession.session_id,
            project_id: failedSession.project_id,
            branch_id: failedSession.branch_id,
            status: failedSession.status,
            current_step: failedSession.current_step,
            completed_scenes: failedSession.completed_scenes,
            head_checkpoint_id: failedSession.checkpoint_id,
            started_at: failedSession.started_at,
            updated_at: failedSession.updated_at,
            termination_reason_text: null,
            restoration_notice_text: null,
            maintenance_status: failedSession.maintenance_status,
            maintenance_error_text: failedSession.maintenance_error_text,
            maintenance_step: failedSession.maintenance_step,
            maintenance_boundary: failedSession.maintenance_boundary,
          },
        ])
      }
      if (path === `/projects/north-star/simulations/${failedSession.session_id}`) {
        return Promise.resolve(failedSession)
      }
      if (
        path ===
          `/projects/north-star/simulations/${failedSession.session_id}/maintenance/retry` &&
        init?.method === 'POST'
      ) {
        return Promise.resolve(succeededSession)
      }
      if (path.includes('/simulation-events')) return Promise.resolve([])
      if (path.includes('/simulation-trace')) return Promise.resolve([])
      throw new Error(`Unexpected request: ${path}`)
    })

    renderEvolution()

    expect(await screen.findByText('Wiki provider timed out')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Retry Wiki maintenance' }))
    await waitFor(() =>
      expect(h.engineRequest).toHaveBeenCalledWith(
        '/projects/north-star/simulations/session:one/maintenance/retry',
        { method: 'POST' }
      )
    )
    await waitFor(() =>
      expect(screen.queryByText('Wiki provider timed out')).not.toBeInTheDocument()
    )
  })

  it('restores the URL-selected terminal session on a non-main branch', async () => {
    setActiveStoryProjectId('north-star')
    window.history.replaceState(
      {},
      '',
      '/evolve?branch=alternate&session=session:terminal'
    )
    const terminal = {
      session_id: 'session:terminal',
      project_id: 'north-star',
      branch_id: 'alternate',
      status: 'terminated',
      pending_control: 'none',
      content_locale: 'zh-CN',
      request: {
        project_id: 'north-star',
        branch_id: 'alternate',
        premise_text: '备用阵列分支',
        actor_ids: ['ara'],
        content_locale: 'zh-CN',
        control: { mode: 'step' },
      },
      current_step: 3,
      completed_scenes: 1,
      roster_actor_ids: ['ara'],
      characters: projectSnapshot.characters,
      pending_scene_events: [],
      actor_states: {},
      game_master_states: {},
      memory_snapshots: {},
      raw_log_offset: 3,
      total_model_tokens: 30,
      consecutive_model_failures: 0,
      checkpoint_id: 'checkpoint-' + 'b'.repeat(64),
      started_at: '2026-08-02T00:00:00Z',
      updated_at: '2026-08-02T00:03:00Z',
      termination_reason_text: 'completed',
      restoration_notice_text: null,
      state_hash: 'b'.repeat(64),
      active_actor_id: null,
      current_action_spec: null,
    }
    h.engineRequest.mockImplementation((path: string) => {
      if (path === '/projects/north-star') return Promise.resolve(projectSnapshot)
      if (path === '/projects/north-star/branches') return Promise.resolve([])
      if (path === '/projects/north-star/simulations') {
        return Promise.resolve([
          {
            session_id: 'session:main-newer',
            project_id: 'north-star',
            branch_id: 'main',
            status: 'paused',
            current_step: 9,
            completed_scenes: 2,
            head_checkpoint_id: 'checkpoint-main',
            started_at: '2026-08-02T00:00:00Z',
            updated_at: '2026-08-02T00:09:00Z',
            termination_reason_text: null,
            restoration_notice_text: null,
          },
          {
            session_id: terminal.session_id,
            project_id: terminal.project_id,
            branch_id: terminal.branch_id,
            status: terminal.status,
            current_step: terminal.current_step,
            completed_scenes: terminal.completed_scenes,
            head_checkpoint_id: terminal.checkpoint_id,
            started_at: terminal.started_at,
            updated_at: terminal.updated_at,
            termination_reason_text: terminal.termination_reason_text,
            restoration_notice_text: null,
          },
        ])
      }
      if (path === '/projects/north-star/simulations/session:terminal') {
        return Promise.resolve(terminal)
      }
      if (path.includes('/simulation-events')) return Promise.resolve([])
      if (path.includes('/simulation-trace')) return Promise.resolve([])
      throw new Error(`Unexpected request: ${path}`)
    })

    renderEvolution()

    expect(await screen.findByText('Step 3')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '历史会话' })).toHaveValue(
      'session:terminal'
    )
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/north-star/simulations/session:terminal'
    )
    expect(window.location.search).toContain('branch=alternate')
    expect(window.location.search).toContain('session=session%3Aterminal')
  })
})

describe('Character workspace', () => {
  beforeEach(() => {
    h.engineRequest.mockReset()
    clearActiveStoryProject()
    window.history.replaceState({}, '', '/')
  })

  it('shows an NPC after the Editor automatically promotes it on the branch', async () => {
    setActiveStoryProjectId('north-star')
    const npc = {
      id: 'temporary-pilot',
      display_name: '临时导航员',
      type: 'npc',
      identity: '赶到观测站的临时导航员',
      core_desire: '帮助观测站恢复通信',
      current_goal: '观察备用阵列',
      known_fact_ids: ['fact:backup-array'],
      relationships: [],
      location: '备用阵列',
      emotional_state: '警觉',
      resources: [],
      version: 2,
    }
    const promotedCharacter = {
      ...npc,
      type: 'active',
      current_goal: '主动校准备用通信阵列',
      version: 3,
    }
    h.engineRequest.mockImplementation((path: string) => {
      if (path === '/projects/north-star') {
        return Promise.resolve(projectSnapshot)
      }
      if (path === '/projects/north-star/characters?branch_id=main') {
        return Promise.resolve(
          [...projectSnapshot.characters, promotedCharacter]
        )
      }
      throw new Error(`Unexpected request: ${path}`)
    })

    render(<CharactersView />)
    fireEvent.click(await screen.findByRole('button', { name: /角色档案/ }))
    fireEvent.click(await screen.findByRole('button', { name: /临时导航员/ }))
    expect(screen.getByText('活跃角色')).toBeInTheDocument()
    expect(screen.getByText('主动校准备用通信阵列')).toBeInTheDocument()
    expect(
      screen.queryByRole('button', { name: '确认升级为活跃角色' })
    ).not.toBeInTheDocument()
  })
})

describe('Character relationship graph', () => {
  beforeEach(() => {
    clearActiveStoryProject()
    h.engineRequest.mockReset()
    h.subscribeProjectEvents.mockReset()
    h.subscribeProjectEvents.mockResolvedValue(() => undefined)
  })

  const relationshipProject = {
    ...projectSnapshot,
    characters: [
      {
        ...projectSnapshot.characters[0],
        relationships: [{ character_id: 'bo', description: '互为退路' }],
      },
      {
        ...projectSnapshot.characters[1],
        relationships: [{ character_id: 'ara', description: '互为退路' }],
      },
    ],
  }

  function mockProject(snapshot: typeof relationshipProject | typeof projectSnapshot) {
    h.engineRequest.mockImplementation((path: string) => {
      if (path === '/projects/north-star') return Promise.resolve(snapshot)
      if (path === '/projects/north-star/characters?branch_id=main') {
        return Promise.resolve(snapshot.characters)
      }
      throw new Error(`Unexpected request: ${path}`)
    })
  }

  it('renders relationships in the character roster', async () => {
    setActiveStoryProjectId('north-star')
    mockProject(relationshipProject)

    render(<CharactersView />)

    fireEvent.click(await screen.findByRole('button', { name: /角色档案/ }))
    expect(await screen.findByText('互为退路')).toBeInTheDocument()
  })

  it('switches to the relationship graph and renders character nodes', async () => {
    setActiveStoryProjectId('north-star')
    mockProject(relationshipProject)

    render(<CharactersView />)

    fireEvent.click(await screen.findByRole('button', { name: /关系图/ }))

    expect(
      screen.getByRole('img', { name: '角色关系图' })
    ).toBeInTheDocument()
    expect(screen.getAllByText('阿岚').length).toBeGreaterThan(0)
  })

  it('shows the empty relationship state when none exist', async () => {
    setActiveStoryProjectId('north-star')
    mockProject(projectSnapshot)

    render(<CharactersView />)

    fireEvent.click(await screen.findByRole('button', { name: /角色档案/ }))
    expect(await screen.findByText('暂无关系记录')).toBeInTheDocument()
  })

  it('shows an empty-edge hint when the graph has no relationship links', async () => {
    setActiveStoryProjectId('north-star')
    mockProject(projectSnapshot)

    render(<CharactersView />)

    fireEvent.click(await screen.findByRole('button', { name: /关系图/ }))

    expect(await screen.findByText('暂无关系连线')).toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: '角色关系图' })
    ).toBeInTheDocument()
  })
})
