import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  engineRequest: vi.fn(),
  subscribeProjectEvents: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}))

vi.mock('./engine', () => ({
  engineRequest: h.engineRequest,
  subscribeProjectEvents: h.subscribeProjectEvents,
}))

vi.mock('@/editor/NovelManuscriptEditor', () => ({
  NovelManuscriptEditor: ({ initialContent }: { initialContent: object }) => (
    <div data-testid="novel-editor">{JSON.stringify(initialContent)}</div>
  ),
}))

import {
  clearActiveStoryProject,
  setActiveStoryProjectId,
} from './activeProject'
import { SimulationHistoryView } from './history/SimulationHistoryView'
import { ManuscriptView } from './manuscript/ManuscriptView'
import { WorldView } from './world/WorldView'

const source = {
  source_id: 'source:main:checkpoint-main-3:0:3',
  branch_id: 'main',
  checkpoint_id: 'checkpoint-main-3',
  from_step: 0,
  to_step: 3,
  boundary: 'scene',
  title_hint: '灯塔机械室',
  event_summary_text: '陈默发现线路被人为切断。',
  event_ids: ['event:session:one:3'],
  estimated_chars: 18,
  available_viewpoint_ids: ['chen-mo'],
  wiki_version_id: 'seed',
  status: 'available',
}

const scene = {
  id: 'scene-000001',
  project_id: 'fog-harbor',
  branch_id: 'main',
  sequence: 1,
  chapter_id: 'chapter-001',
  title: '切断的线路',
  body: '陈默在机械室里发现断线。',
  source: {
    project_id: 'fog-harbor',
    branch_id: 'main',
    checkpoint_id: 'checkpoint-main-3',
    source_ids: ['source:main:checkpoint-main-3:0:3'],
    from_step: 0,
    to_step: 3,
    event_ids: ['event:session:one:3'],
    memory_ids: ['memory:gm:3'],
    wiki_version_id: 'seed',
    viewpoint_actor_id: 'chen-mo',
  },
  context_manifest: {
    wiki_page_paths: [],
    memory_ids: ['memory:gm:3'],
    previous_scene_id: null,
    selected_source_ids: ['source:main:checkpoint-main-3:0:3'],
    selection_reason: 'complete scene source selection',
    target_words: null,
    instruction: null,
  },
  base_scene_version: 0,
  revision: 0,
  review: null,
  status: 'draft',
}

describe('closed-loop story views', () => {
  beforeEach(() => {
    clearActiveStoryProject()
    h.engineRequest.mockReset()
    h.subscribeProjectEvents.mockReset()
    h.subscribeProjectEvents.mockResolvedValue(() => undefined)
    window.history.replaceState({}, '', '/')
  })

  it('renders a branch-scoped World Wiki and saves director instructions', async () => {
    setActiveStoryProjectId('fog-harbor')
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path.endsWith('/wiki')) {
        return Promise.resolve({
          branch_id: 'main',
          checkpoint_id: 'checkpoint-main-3',
          updated_at_step: 3,
          stale: false,
          pages: [
            {
              path: 'world/state.md',
              title: '世界状态',
              subject_id: null,
              updated_at_step: 3,
              source_ids: ['event:session:one:3'],
              confidence: 0.92,
            },
          ],
        })
      }
      if (path.includes('/wiki/page?path=world%2Fstate.md')) {
        return Promise.resolve({
          branch_id: 'main',
          path: 'world/state.md',
          subject_id: null,
          updated_at_step: 3,
          source_ids: ['event:session:one:3'],
          confidence: 0.92,
          checkpoint_id: 'checkpoint-main-3',
          stale: false,
          content: '# 世界状态\n\n暴风雨逼近雾港。\n\n灯塔线路被人为切断。',
        })
      }
      if (path.endsWith('/director-instructions') && !init) {
        return Promise.resolve([])
      }
      if (path.endsWith('/director-instructions') && init?.method === 'POST') {
        expect(init.body).toBe(
          JSON.stringify({
            checkpoint_id: 'checkpoint-main-3',
            text: '下一场让潮汐成为压力。',
          })
        )
        return Promise.resolve({
          instruction_id: 'director:one',
          text: '下一场让潮汐成为压力。',
          created_at: '2026-08-03T00:01:00Z',
          applies_from_checkpoint_id: 'checkpoint-main-3',
        })
      }
      throw new Error(`Unexpected request: ${path}`)
    })

    render(<WorldView />)
    expect(await screen.findByText('暴风雨逼近雾港。')).toBeInTheDocument()
    expect(screen.getByText('灯塔线路被人为切断。')).toBeInTheDocument()
    expect(screen.getByText('event:session:one:3')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '导演补充' }))
    const note = await waitFor(() => {
      const textarea = screen
        .getAllByRole('textbox')
        .find((element) => element.tagName === 'TEXTAREA')
      expect(textarea).toBeDefined()
      return textarea!
    })
    fireEvent.change(note, {
      target: { value: '下一场让潮汐成为压力。' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存导演补充' }))
    expect(
      await screen.findByText('下一场让潮汐成为压力。')
    ).toBeInTheDocument()
  })

  it('shows a degraded Wiki state and explains that manuscript generation is paused', async () => {
    setActiveStoryProjectId('fog-harbor')
    h.engineRequest.mockImplementation((path: string) => {
      if (path.endsWith('/wiki')) {
        return Promise.resolve({
          branch_id: 'main',
          checkpoint_id: 'checkpoint-main-3',
          updated_at_step: 3,
          stale: true,
          degraded: true,
          degradation_reason: 'Wiki proposal failed after 2 attempts',
          pages: [],
        })
      }
      if (path.endsWith('/director-instructions')) return Promise.resolve([])
      throw new Error(`Unexpected request: ${path}`)
    })

    render(<WorldView />)

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Wiki 维护已降级'
    )
    expect(screen.getByRole('status')).toHaveTextContent('正文生成已暂停')
    expect(screen.getByRole('status')).toHaveTextContent(
      'Wiki proposal failed after 2 attempts'
    )
  })

  it('generates a manuscript scene from a frozen source with lineage', async () => {
    setActiveStoryProjectId('fog-harbor')
    let scenes: object[] = []
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path.endsWith('/manuscript/sources')) return Promise.resolve([source])
      if (path.endsWith('/manuscript/scenes') && !init) {
        return Promise.resolve(scenes)
      }
      if (path.endsWith('/manuscript/scenes') && init?.method === 'POST') {
        expect(init?.body).toBe(
          JSON.stringify({
            source: {
              mode: 'scene',
              source_id: 'source:main:checkpoint-main-3:0:3',
            },
            chapter_id: 'chapter-001',
            viewpoint_actor_id: null,
            target_words: null,
            instruction: null,
          })
        )
        scenes = [scene]
        return Promise.resolve(scene)
      }
      throw new Error(`Unexpected request: ${path}`)
    })

    render(<ManuscriptView />)
    expect(
      await screen.findByText('陈默发现线路被人为切断。')
    ).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('来源模式'), {
      target: { value: 'scene' },
    })
    fireEvent.click(
      await screen.findByRole('button', { name: '从冻结来源生成' })
    )
    expect(await screen.findByDisplayValue('切断的线路')).toBeInTheDocument()
    expect(screen.getByText('checkpoint-main-3')).toBeInTheDocument()
    expect(screen.getByText('event:session:one:3')).toBeInTheDocument()
  })

  it('renders ResolvedEvent history instead of the removed formal-event feed', async () => {
    setActiveStoryProjectId('fog-harbor')
    h.engineRequest.mockResolvedValue([
      {
        checkpoint_id: 'checkpoint-main-3',
        result: {
          session_id: 'session:one',
          branch_id: 'main',
          step: 3,
          acting_actor_id: 'chen-mo',
          action_spec: null,
          action_text: null,
          status: 'paused',
          boundary: 'scene',
          resolved_turn: {
            raw_resolution_text: '陈默确认线路被人为切断。',
            events: [
              {
                event_id: 'event:session:one:3',
                event_text: '灯塔线路被人为切断。',
                participant_ids: ['chen-mo'],
                visibility: 'participants',
              },
            ],
          },
        },
        trace: {
          trace_id: 'trace:one',
          stages: [{ stage_id: 'stage:one' }],
          model_calls: [{ call_id: 'call:one' }],
        },
      },
    ])

    render(<SimulationHistoryView />)
    expect(
      await screen.findByText('陈默确认线路被人为切断。')
    ).toBeInTheDocument()
    expect(screen.getByText('灯塔线路被人为切断。')).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/fog-harbor/branches/main/simulation-trace?after_step=-1'
    )
  })

  it('shows project-free empty states without issuing backend requests', () => {
    render(<WorldView />)
    expect(screen.getByText('先选择一个故事项目。')).toBeInTheDocument()
    expect(h.engineRequest).not.toHaveBeenCalled()
  })
})
