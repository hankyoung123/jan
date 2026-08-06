import { render, screen } from '@testing-library/react'
import type { ComponentType, ReactNode } from 'react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@tanstack/react-router', () => ({
  createFileRoute:
    () =>
    (config: { component: ComponentType }) => ({
      ...config,
      id: '/',
    }),
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))

vi.mock('@/features/story/engine', () => ({
  engineRequest: vi.fn().mockResolvedValue([]),
  subscribeProjectEvents: vi.fn().mockResolvedValue(() => undefined),
}))

import { Route } from '../index'

describe('Story workbench route', () => {
  it('renders the Markdown project workbench instead of the Jan chat home', async () => {
    const Component = Route.component as ComponentType
    render(<Component />)

    expect(
      screen.getByRole('heading', { level: 1, name: '工作台' })
    ).toBeInTheDocument()
    expect(
      await screen.findByRole('heading', {
        level: 2,
        name: '选择一个项目开始工作',
      })
    ).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /推进下一轮/ })).not.toBeInTheDocument()
    expect(screen.queryByTestId('chat-input')).not.toBeInTheDocument()
  })
})
