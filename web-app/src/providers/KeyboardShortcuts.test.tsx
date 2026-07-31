import { render } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  navigate: vi.fn(),
  shortcuts: [] as Array<{ key: string; callback?: () => void }>,
}))

vi.mock('@/hooks/useHotkeys', () => ({
  useKeyboardShortcut: (shortcut: { key: string; callback?: () => void }) => {
    h.shortcuts.push(shortcut)
  },
}))
vi.mock('@/hooks/useLeftPanel', () => ({
  useLeftPanel: () => ({ open: true, setLeftPanel: vi.fn() }),
}))
vi.mock('@tanstack/react-router', () => ({
  useRouter: () => ({ navigate: h.navigate }),
}))

import { KeyboardShortcutsProvider } from './KeyboardShortcuts'

describe('Story keyboard shortcuts', () => {
  beforeEach(() => {
    h.navigate.mockReset()
    h.shortcuts.length = 0
  })

  it('opens Story submission and registers no Jan chat shortcuts', () => {
    render(<KeyboardShortcutsProvider />)

    const registeredKeys = h.shortcuts.map(({ key }) => key)
    expect(registeredKeys).toEqual(['b', 'p', ','])
    expect(registeredKeys).not.toContain('n')
    expect(registeredKeys).not.toContain('j')
    expect(registeredKeys).not.toContain('k')

    h.shortcuts.find(({ key }) => key === 'p')?.callback?.()
    expect(h.navigate).toHaveBeenCalledWith({ to: '/submission' })
  })
})
