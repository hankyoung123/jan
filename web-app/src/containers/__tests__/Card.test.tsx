import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { CardItem } from '../Card'

describe('CardItem', () => {
  it('stacks content and actions on narrow screens', () => {
    const { container } = render(
      <CardItem
        title="Logs"
        description="Application logs"
        actions={<button type="button">Open logs</button>}
      />
    )

    expect(container.firstElementChild).toHaveClass(
      'flex-col',
      'sm:flex-row'
    )
    expect(screen.getByRole('button', { name: 'Open logs' }).parentElement).toHaveClass(
      'w-full',
      'sm:w-auto'
    )
  })
})
