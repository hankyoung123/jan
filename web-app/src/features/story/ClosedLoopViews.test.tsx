import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({ engineRequest: vi.fn() }))

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to }: { children: ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}))

vi.mock('./engine', () => ({ engineRequest: h.engineRequest }))

vi.mock('@/editor/NovelManuscriptEditor', () => ({
  NovelManuscriptEditor: ({ initialContent }: { initialContent: object }) => (
    <div data-testid="novel-editor">{JSON.stringify(initialContent)}</div>
  ),
}))

import { clearActiveStoryProject, setActiveStoryProjectId } from './activeProject'
import { SimulationHistoryView } from './history/SimulationHistoryView'
import { ManuscriptView } from './manuscript/ManuscriptView'
import { WorldView } from './world/WorldView'

const source = {
  source_id: 'source:main:0:3',
  branch_id: 'main',
  checkpoint_id: 'checkpoint-main-3',
  from_step: 0,
  to_step: 3,
  boundary: 'scene',
  title_hint: '灯塔机械室',
  event_summary_text: '陈默发现线路被人为切断。',
  available_viewpoint_ids: ['chen-mo'],
  already_written: false,
}

const scene = {
  id: 'scene-000001',
  project_id: 'fog-harbor',
  branch_id: 'main',
  sequence: 1,
  chapter_id: 'chapter-001',
  title: '切断的线路',
  body: '陈默在机械室里发现断线。',
  source_checkpoint_id: 'checkpoint-main-3',
  source_from_step: 0,
  source_to_step: 3,
  source_event_ids: ['event:session:one:3'],
  source_memory_ids: ['memory:gm:3'],
  viewpoint_actor_id: 'chen-mo',
  base_scene_version: 0,
  revision: 0,
  review: null,
  status: 'draft',
}

const worldEntry = {
  entry_id: 'world-fact:wire',
  category: 'established_fact',
  title: '被切断的线路',
  content_text: '灯塔线路被人为切断。',
  source_record_ids: ['memory:gm:3'],
  source_event_ids: ['event:session:one:3'],
  first_seen_step: 3,
  last_updated_step: 3,
  confidence: 0.92,
  status: 'active',
}

describe('closed-loop story views', () => {
  beforeEach(() => {
    clearActiveStoryProject()
    h.engineRequest.mockReset()
    window.history.replaceState({}, '', '/')
  })

  it('renders a branch-scoped World Bible and saves director instructions', async () => {
    setActiveStoryProjectId('fog-harbor')
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path.endsWith('/world-bible')) {
        return Promise.resolve({
          project_id: 'fog-harbor',
          branch_id: 'main',
          checkpoint_id: 'checkpoint-main-3',
          creative_direction_text: '悬疑 · 克制',
          current_world_state_text: '暴风雨逼近雾港。',
          rules: [],
          locations: [],
          organizations: [],
          history: [],
          established_facts: [worldEntry],
          unresolved_threads: [],
          generated_at: '2026-08-03T00:00:00Z',
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
    fireEvent.click(screen.getByRole('button', { name: '已确立事实' }))
    fireEvent.click(await screen.findByRole('button', { name: /被切断的线路/ }))
    expect(screen.getByText('灯塔线路被人为切断。')).toBeInTheDocument()
    expect(screen.getByText('event:session:one:3')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '导演补充' }))
    const note = screen
      .getAllByRole('textbox')
      .find((element) => element.tagName === 'TEXTAREA')!
    fireEvent.change(note, {
      target: { value: '下一场让潮汐成为压力。' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存导演补充' }))
    expect(await screen.findByText('下一场让潮汐成为压力。')).toBeInTheDocument()
  })

  it('generates manuscript directly from a Narrative Source with lineage', async () => {
    setActiveStoryProjectId('fog-harbor')
    let scenes: object[] = []
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path.endsWith('/narrative-sources')) return Promise.resolve([source])
      if (path.endsWith('/manuscript/scenes') && !init) {
        return Promise.resolve(scenes)
      }
      if (path.endsWith('/manuscript/scenes/generate')) {
        expect(init?.body).toBe(
          JSON.stringify({
            checkpoint_id: 'checkpoint-main-3',
            from_step: 0,
            to_step: 3,
            chapter_id: 'chapter-001',
            viewpoint_actor_id: null,
          })
        )
        scenes = [scene]
        return Promise.resolve(scene)
      }
      throw new Error(`Unexpected request: ${path}`)
    })

    render(<ManuscriptView />)
    expect(await screen.findByText('陈默发现线路被人为切断。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '从此片段生成' }))
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
    expect(await screen.findByText('陈默确认线路被人为切断。')).toBeInTheDocument()
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
