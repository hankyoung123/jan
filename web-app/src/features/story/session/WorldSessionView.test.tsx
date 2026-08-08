import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({ engineRequest: vi.fn() }))

vi.mock('../engine', () => ({ engineRequest: h.engineRequest }))

import { WorldSessionView } from './WorldSessionView'

const response = {
  perception: {
    scene_text: '雨水浸透了门口的地毯。',
    player_state_summary: '本地调查记者',
    visible_changes: [],
    checkpoint_id: 'checkpoint:1',
    world_time: '18:43',
  },
  visible_events: [],
  player_state: {
    identity: '本地调查记者',
    capabilities: ['调查采访', '摄影'],
    conditions: ['右手轻伤'],
    possessions: ['手机', '相机'],
    relationships: ['林澈是你的旧友。'],
  },
  checkpoint_id: 'checkpoint:1',
  world_time: '18:43',
}

const branches = [{ branch_id: 'main', head_checkpoint_id: 'checkpoint:1', content_locale: 'zh-CN' }]
const timeline = [{ checkpoint_id: 'checkpoint:1', step: 0, world_time: '18:43', is_current: true }]

describe('WorldSessionView', () => {
  beforeEach(() => {
    h.engineRequest.mockReset()
  })

  it('opens a perception-only world session and sends free natural language', async () => {
    h.engineRequest.mockImplementation((path: string) => {
      if (path.endsWith('/simulation/session')) return Promise.resolve(response)
      if (path.endsWith('/branches')) return Promise.resolve(branches)
      if (path.endsWith('/timeline')) return Promise.resolve(timeline)
      if (path.endsWith('/simulation/turn')) {
        return Promise.resolve({
          ...response,
          visible_events: ['张野仍在柜台附近。'],
          perception: {
            ...response.perception,
            visible_changes: ['张野仍在柜台附近。'],
            checkpoint_id: 'checkpoint:2',
          },
          checkpoint_id: 'checkpoint:2',
        })
      }
      return Promise.reject(new Error(`unexpected request: ${path}`))
    })

    render(<WorldSessionView />)

    expect(await screen.findByText('雨水浸透了门口的地毯。')).toBeInTheDocument()
    expect(screen.getByText('本地调查记者')).toBeInTheDocument()
    expect(screen.queryByText('调查桌子')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('你的意图'), {
      target: { value: '我走过去看看桌上的东西' },
    })
    fireEvent.click(screen.getByRole('button', { name: '提交意图' }))

    expect(await screen.findByText('张野仍在柜台附近。')).toBeInTheDocument()
    await waitFor(() => {
      expect(h.engineRequest).toHaveBeenCalledWith(
        '/projects/last-ferry-before/simulation/turn',
        {
          method: 'POST',
          body: JSON.stringify({ text: '我走过去看看桌上的东西' }),
        }
      )
    })
  })

  it('forks from an earlier committed checkpoint and opens the new branch', async () => {
    const branchTimeline = [
      { checkpoint_id: 'checkpoint:0', step: 0, world_time: '18:43', is_current: false },
      { checkpoint_id: 'checkpoint:1', step: 2, world_time: '18:49', is_current: true },
    ]
    const alternate = { branch_id: 'fork-kf12oi', head_checkpoint_id: 'checkpoint:0', content_locale: 'zh-CN' }
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/projects/last-ferry-before/simulation/session') return Promise.resolve(response)
      if (path === '/projects/last-ferry-before/simulation/session?branch_id=fork-kf12oi') {
        return Promise.resolve({ ...response, checkpoint_id: 'checkpoint:fork' })
      }
      if (path === '/projects/last-ferry-before/branches') {
        return Promise.resolve(init?.method === 'POST' ? alternate : [...branches, alternate])
      }
      if (path === '/projects/last-ferry-before/branches/main/timeline') return Promise.resolve(branchTimeline)
      if (path === '/projects/last-ferry-before/branches/fork-kf12oi/timeline') {
        return Promise.resolve([{ ...branchTimeline[0], is_current: true }])
      }
      return Promise.reject(new Error(`unexpected request: ${path}`))
    })
    vi.spyOn(Date, 'now').mockReturnValue(1234567890)

    render(<WorldSessionView />)

    fireEvent.click(await screen.findByRole('button', { name: '从 Step 0 创建分支' }))

    await waitFor(() => {
      const call = h.engineRequest.mock.calls.find(([path, init]) => (
        path === '/projects/last-ferry-before/branches' && init?.method === 'POST'
      ))
      expect(call).toBeDefined()
      expect(JSON.parse(call?.[1].body)).toMatchObject({
        branch_id: 'fork-kf12oi',
        source_checkpoint_id: 'checkpoint:0',
        parent_branch_id: 'main',
      })
    })
    expect(await screen.findByDisplayValue('fork-kf12oi')).toBeInTheDocument()
    expect(h.engineRequest).toHaveBeenCalledWith(
      '/projects/last-ferry-before/simulation/session?branch_id=fork-kf12oi'
    )
  })
})
