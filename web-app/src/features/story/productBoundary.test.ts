import { describe, expect, it } from 'vitest'

import { primaryRoutes, storyRoutes } from '@/components/left-sidebar/navigation'
import { retiredJanProductRoutes } from './productBoundary'

describe('Story product boundary', () => {
  it('retires every inherited Jan chat product route', () => {
    expect(retiredJanProductRoutes).toEqual({
      thread: {
        path: '/threads/$threadId',
        destination: '/',
      },
      project: {
        path: '/project/$projectId',
        destination: '/submission',
      },
      assistant: {
        path: '/settings/assistant',
        destination: '/settings/general',
      },
    })
  })

  it('keeps retired Jan routes out of Story navigation', () => {
    const visibleRoutes = [...primaryRoutes, ...storyRoutes].map(({ url }) => url)

    for (const retiredRoute of Object.values(retiredJanProductRoutes)) {
      expect(visibleRoutes).not.toContain(retiredRoute.path)
    }
  })
})
