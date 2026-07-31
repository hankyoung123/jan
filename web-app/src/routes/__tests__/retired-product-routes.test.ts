import { describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  redirect: vi.fn((options: { to: string }) => ({ type: 'redirect', ...options })),
}))

vi.mock('@tanstack/react-router', () => ({
  createFileRoute:
    (path: string) =>
    (configuration: { beforeLoad: () => never }) => ({
      ...configuration,
      path,
    }),
  redirect: h.redirect,
}))

import { Route as AssistantRoute } from '../settings/assistant'
import { Route as ProjectRoute } from '../project/$projectId'
import { Route as ThreadRoute } from '../threads/$threadId'

function redirectedTo(routeModule: unknown): string {
  const route = routeModule as { beforeLoad: () => never }
  try {
    route.beforeLoad()
  } catch (result) {
    return (result as { to: string }).to
  }
  throw new Error('retired route did not redirect')
}

describe('retired Jan product routes', () => {
  it('redirects chat threads to the Story workbench', () => {
    expect(redirectedTo(ThreadRoute)).toBe('/')
  })

  it('redirects chat projects to Story submission', () => {
    expect(redirectedTo(ProjectRoute)).toBe('/submission')
  })

  it('redirects Assistant settings to retained global settings', () => {
    expect(redirectedTo(AssistantRoute)).toBe('/settings/general')
  })
})
