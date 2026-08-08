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

describe('WorldSessionView', () => {
  beforeEach(() => {
    h.engineRequest.mockReset()
  })

  it('opens a perception-only world session and sends free natural language', async () => {
    h.engineRequest
      .mockResolvedValueOnce(response)
      .mockResolvedValueOnce({
        ...response,
        visible_events: ['张野仍在柜台附近。'],
        perception: {
          ...response.perception,
          visible_changes: ['张野仍在柜台附近。'],
          checkpoint_id: 'checkpoint:2',
        },
        checkpoint_id: 'checkpoint:2',
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
      expect(h.engineRequest).toHaveBeenLastCalledWith(
        '/projects/last-ferry-before/simulation/turn',
        {
          method: 'POST',
          body: JSON.stringify({ text: '我走过去看看桌上的东西' }),
        }
      )
    })
  })
})
