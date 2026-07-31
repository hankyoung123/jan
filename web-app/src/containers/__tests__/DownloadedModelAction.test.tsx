import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/i18n', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

import { DownloadedModelAction } from '../DownloadedModelAction'

describe('DownloadedModelAction', () => {
  it('reports readiness without exposing a generic chat action', () => {
    render(<DownloadedModelAction />)

    const state = screen.getByRole('button', { name: 'hub:downloaded' })
    expect(state).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'hub:newChat' })).not.toBeInTheDocument()
  })
})
