import '@testing-library/jest-dom/vitest'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/containers/SettingsMenu', () => ({
  default: () => <nav data-testid="settings-menu" />,
}))
vi.mock('@/containers/HeaderPage', () => ({
  default: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
}))
vi.mock('@/features/story/ModelProfiles', () => ({
  ModelProfiles: () => <div data-testid="model-profiles" />,
}))
vi.mock('@/constants/routes', () => ({
  route: { settings: { story_models: '/settings/story-models' } },
}))
vi.mock('@tanstack/react-router', () => ({
  createFileRoute: (path: string) => (config: Record<string, unknown>) => ({
    ...config,
    path,
  }),
}))

import { Route } from '../story-models'

describe('Story Agent model settings route', () => {
  it('is reachable through the settings route and renders the editor', () => {
    expect((Route as { path: string }).path).toBe('/settings/story-models')
    const Component = Route.component as React.ComponentType
    render(<Component />)

    expect(screen.getByTestId('settings-menu')).toBeInTheDocument()
    expect(screen.getByTestId('model-profiles')).toBeInTheDocument()
    expect(screen.getByText('Story Agent 模型')).toBeInTheDocument()
  })
})
