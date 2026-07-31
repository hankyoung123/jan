import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({ engineRequest: vi.fn() }))

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

vi.mock('./engine', () => ({ engineRequest: h.engineRequest }))

vi.mock('@/containers/MessageItem', () => ({
  MessageItem: ({ message }: { message: { parts: Array<{ type: string; text?: string }> } }) => (
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
import { EvolutionView, ManuscriptView, SubmissionView } from './StoryViews'

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

const manuscriptReview = {
  review: {
    mode: 'manuscript_review',
    passed: true,
    summary: '正文仅使用了已确认事实。',
    issues: [],
  },
  new_facts: [],
}

const storyEvents = [
  {
    id: 'event-000004',
    sequence: 4,
    source_turn_id: 'turn-04',
    occurred_at: '2026-07-31T12:00:00Z',
    summary: '阿岚在主天线里找到烧蚀的校验模块。',
    participants: ['ara'],
    public_results: ['主天线校验模块已经烧毁'],
    hidden_results: [],
    character_changes: [],
    world_changes: [],
    approved_by_user: true,
  },
  {
    id: 'event-000005',
    sequence: 5,
    source_turn_id: 'turn-05',
    occurred_at: '2026-07-31T12:05:00Z',
    summary: '柏舟启用了最后一套备用氧气循环。',
    participants: ['bo'],
    public_results: ['备用氧气循环已经启动'],
    hidden_results: [],
    character_changes: [],
    world_changes: [],
    approved_by_user: true,
  },
]

const sceneDraft = {
  id: 'scene-000004',
  project_id: 'north-star',
  sequence: 4,
  chapter_id: 'chapter-001',
  title: '极光下的校验模块',
  body: '阿岚拆开主天线的防护壳。\n\n烧蚀的校验模块仍有余温。',
  source_event_ids: ['event-000004'],
  base_world_version: 4,
  base_scene_version: 2,
  revision: 2,
  review: manuscriptReview,
  amendment_id: null,
  status: 'saved',
} as const

function mockManuscriptWorkspace(
  scenes: ReadonlyArray<typeof sceneDraft> = [sceneDraft]
) {
  h.engineRequest.mockImplementation((path: string) => {
    if (path === '/projects/north-star/events') {
      return Promise.resolve(storyEvents)
    }
    if (path === '/projects/north-star/scenes') {
      return Promise.resolve(scenes)
    }
    throw new Error(`Unexpected request: ${path}`)
  })
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
            passed: true,
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
      await screen.findByText('初始世界已经具备运行条件，可以继续调整或创建项目。')
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

describe('Manuscript workspace', () => {
  beforeEach(() => {
    h.engineRequest.mockReset()
    clearActiveStoryProject()
  })

  it('requires an active project and does not load fixture data', () => {
    render(<ManuscriptView />)

    expect(screen.getByText('尚未选择故事项目')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '前往投稿' })).toHaveAttribute(
      'href',
      '/submission'
    )
    expect(h.engineRequest).not.toHaveBeenCalled()
  })

  it('loads canonical events and scenes into the Novel and shadcn workspace', async () => {
    setActiveStoryProjectId('north-star')
    mockManuscriptWorkspace()
    render(<ManuscriptView />)

    expect(await screen.findByDisplayValue(sceneDraft.title)).toBeInTheDocument()
    expect(
      await screen.findByTestId(
        'novel-manuscript-editor',
        {},
        { timeout: 5000 }
      )
    ).toBeInTheDocument()
    expect(
      screen.getByRole('toolbar', { name: '正文格式工具栏' })
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '粗体' })).toBeEnabled()
    expect(screen.getByText(storyEvents[0].summary)).toBeInTheDocument()
    expect(screen.getByText('章节与场景')).toBeInTheDocument()
    expect(screen.getByText('事实来源')).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith('/projects/north-star/events')
    expect(h.engineRequest).toHaveBeenCalledWith('/projects/north-star/scenes')
  })

  it('generates only from the latest unrepresented confirmed event', async () => {
    setActiveStoryProjectId('north-star')
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/projects/north-star/events') {
        return Promise.resolve(storyEvents)
      }
      if (path === '/projects/north-star/scenes') return Promise.resolve([])
      if (path === '/projects/north-star/scenes/generate') {
        expect(JSON.parse(String(init?.body))).toEqual({
          event_ids: ['event-000005'],
          chapter_id: 'chapter-001',
        })
        return Promise.resolve({
          ...sceneDraft,
          id: 'scene-000001',
          sequence: 1,
          source_event_ids: ['event-000005'],
          title: '最后的氧气循环',
          status: 'reviewed',
        })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    render(<ManuscriptView />)

    expect(await screen.findByText('从确认事件生成第一幕')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '从事件生成' }))

    expect(await screen.findByDisplayValue('最后的氧气循环')).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/north-star/scenes/generate',
      expect.objectContaining({ method: 'POST' })
    )
  })

  it('saves edited Markdown with draft and canonical scene versions', async () => {
    setActiveStoryProjectId('north-star')
    const savedDraft = {
      ...sceneDraft,
      title: '极光中的校验模块',
      revision: 3,
    }
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/projects/north-star/events') return Promise.resolve(storyEvents)
      if (path === '/projects/north-star/scenes') return Promise.resolve([sceneDraft])
      if (path === '/projects/north-star/scenes/scene-000004') {
        const request = JSON.parse(String(init?.body))
        expect(request).toEqual({
          title: savedDraft.title,
          body: sceneDraft.body,
          expected_revision: 2,
          expected_scene_version: 2,
        })
        return Promise.resolve({
          status: 'saved',
          draft: savedDraft,
          review: manuscriptReview,
          scene: {
            id: sceneDraft.id,
            project_id: sceneDraft.project_id,
            sequence: sceneDraft.sequence,
            chapter_id: sceneDraft.chapter_id,
            title: savedDraft.title,
            body: sceneDraft.body,
            source_event_ids: sceneDraft.source_event_ids,
            version: 3,
          },
          amendment: null,
        })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    render(<ManuscriptView />)
    await screen.findByDisplayValue(sceneDraft.title)

    fireEvent.change(screen.getByRole('textbox', { name: '场景标题' }), {
      target: { value: savedDraft.title },
    })

    expect(screen.getByText('存在未保存更改')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '保存并检查事实' }))

    expect(
      await screen.findByText('事实检查通过，正式 Markdown 已保存')
    ).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/north-star/scenes/scene-000004',
      expect.objectContaining({ method: 'PUT' })
    )
  })

  it('keeps new facts out of Canon until the user confirms the Amendment', async () => {
    setActiveStoryProjectId('north-star')
    const amendmentReview = {
      review: {
        mode: 'manuscript_review',
        passed: false,
        summary: '正文包含尚未进入 Canon 的新事实。',
        issues: [
          {
            code: 'new_fact_requires_amendment',
            message: '新增事实必须先确认。',
            severity: 'blocking',
          },
        ],
      },
      new_facts: ['校验模块内藏着一枚旧徽章'],
    }
    const amendmentId = 'amendment-scene-000004-000003'
    const amendmentDraft = {
      ...sceneDraft,
      title: '极光中的旧徽章',
      revision: 3,
      review: amendmentReview,
      amendment_id: amendmentId,
      status: 'amendment_required',
    }
    const committedEvent = {
      ...storyEvents[1],
      id: 'event-000006',
      sequence: 6,
      summary: '校验模块内藏着一枚旧徽章。',
    }
    const refreshedDraft = {
      ...amendmentDraft,
      base_scene_version: 3,
      review: manuscriptReview,
      amendment_id: null,
      status: 'saved',
    }
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/projects/north-star/events') return Promise.resolve(storyEvents)
      if (path === '/projects/north-star/scenes') return Promise.resolve([sceneDraft])
      if (
        path === '/projects/north-star/scenes/scene-000004' &&
        init?.method === 'PUT'
      ) {
        return Promise.resolve({
          status: 'amendment_required',
          draft: amendmentDraft,
          review: amendmentReview,
          scene: null,
          amendment: {
            id: amendmentId,
            project_id: 'north-star',
            scene_id: sceneDraft.id,
            source_event_ids: sceneDraft.source_event_ids,
            proposed_facts: amendmentReview.new_facts,
            fact_ids: ['fact:old-badge'],
            base_world_version: 4,
            draft_revision: 3,
            status: 'pending',
          },
        })
      }
      if (path.endsWith(`/${amendmentId}/confirm`)) {
        return Promise.resolve({
          scene: {
            id: sceneDraft.id,
            project_id: sceneDraft.project_id,
            sequence: sceneDraft.sequence,
            chapter_id: sceneDraft.chapter_id,
            title: amendmentDraft.title,
            body: amendmentDraft.body,
            source_event_ids: sceneDraft.source_event_ids,
            version: 3,
          },
          amendment: {
            id: amendmentId,
            project_id: 'north-star',
            scene_id: sceneDraft.id,
            source_event_ids: sceneDraft.source_event_ids,
            proposed_facts: amendmentReview.new_facts,
            fact_ids: ['fact:old-badge'],
            base_world_version: 4,
            draft_revision: 3,
            status: 'committed',
          },
          event: committedEvent,
        })
      }
      if (path === '/projects/north-star/scenes/scene-000004') {
        return Promise.resolve(refreshedDraft)
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    render(<ManuscriptView />)
    await screen.findByDisplayValue(sceneDraft.title)

    fireEvent.change(screen.getByRole('textbox', { name: '场景标题' }), {
      target: { value: amendmentDraft.title },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存并检查事实' }))

    expect(await screen.findByText(amendmentReview.new_facts[0])).toBeInTheDocument()
    expect(
      screen.getByText('检测到新事实，确认 Amendment 前正式正文不会改变')
    ).toBeInTheDocument()
    expect(screen.queryByText(/正式 Markdown 已保存/)).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '确认 Amendment' }))

    expect(
      await screen.findByText(
        `${committedEvent.id} 已确认，世界、事件与正式正文已原子写入`
      )
    ).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith(
      `/projects/north-star/scenes/scene-000004/amendments/${amendmentId}/confirm`,
      { method: 'POST' }
    )
  })

  it('exports canonical scenes as a Markdown download', async () => {
    setActiveStoryProjectId('north-star')
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined)
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:manuscript'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
    h.engineRequest.mockImplementation((path: string) => {
      if (path === '/projects/north-star/events') return Promise.resolve(storyEvents)
      if (path === '/projects/north-star/scenes') return Promise.resolve([sceneDraft])
      if (path === '/projects/north-star/manuscript/export') {
        return Promise.resolve({
          filename: 'north-star-manuscript.md',
          markdown: `# 北辰\n\n${sceneDraft.body}`,
        })
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    render(<ManuscriptView />)
    await screen.findByDisplayValue(sceneDraft.title)

    fireEvent.click(screen.getByRole('button', { name: '导出 Markdown' }))

    expect(
      await screen.findByText('已导出 north-star-manuscript.md')
    ).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/north-star/manuscript/export'
    )
    expect(click).toHaveBeenCalledOnce()

    click.mockRestore()
  })
})
