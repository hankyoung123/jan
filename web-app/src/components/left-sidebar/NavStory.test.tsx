import { fireEvent, render, screen } from '@testing-library/react'
import type { ComponentProps, ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  isMobile: true,
  setOpenMobile: vi.fn(),
}))

vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    onClick,
    to,
    ...props
  }: ComponentProps<'a'> & { to: string }) => (
    <a
      href={to}
      onClick={(event) => {
        event.preventDefault()
        onClick?.(event)
      }}
      {...props}
    >
      {children}
    </a>
  ),
  useRouterState: ({ select }: { select: (state: unknown) => unknown }) =>
    select({ location: { pathname: '/' } }),
}))

vi.mock('@/components/ui/sidebar', () => {
  const Wrapper = ({ children }: { children: ReactNode }) => <div>{children}</div>
  const MenuButton = ({ children }: ComponentProps<'div'>) => <>{children}</>
  return {
    SidebarGroup: Wrapper,
    SidebarGroupContent: Wrapper,
    SidebarGroupLabel: Wrapper,
    SidebarMenu: Wrapper,
    SidebarMenuButton: MenuButton,
    SidebarMenuItem: Wrapper,
    useSidebar: () => ({
      isMobile: h.isMobile,
      setOpenMobile: h.setOpenMobile,
    }),
  }
})

import { NavMain } from './NavMain'
import { NavStory } from './NavStory'

describe('mobile story navigation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    h.isMobile = true
  })

  it('closes the mobile drawer after choosing a story route', () => {
    render(<NavStory />)

    fireEvent.click(screen.getByRole('link', { name: '推进故事' }))

    expect(h.setOpenMobile).toHaveBeenCalledWith(false)
  })

  it('closes the mobile drawer after choosing a primary route', () => {
    render(<NavMain />)

    fireEvent.click(screen.getByRole('link', { name: '模型中心' }))

    expect(h.setOpenMobile).toHaveBeenCalledWith(false)
  })

  it('does not change mobile drawer state for desktop navigation', () => {
    h.isMobile = false
    render(<NavStory />)

    fireEvent.click(screen.getByRole('link', { name: '角色' }))

    expect(h.setOpenMobile).not.toHaveBeenCalled()
  })
})
