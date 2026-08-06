import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { NovelManuscriptEditor } from './NovelManuscriptEditor'
import {
  novelDocumentFromMarkdown,
  novelDocumentToMarkdown,
} from './manuscriptMarkdown'

vi.stubGlobal(
  'ResizeObserver',
  class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
)

describe('Novel manuscript Markdown boundary', () => {
  it('round-trips the formatting exposed by the shadcn toolbar', () => {
    const markdown = [
      '## 风暴中的灯塔',
      '',
      '陈默看见了**新鲜刮痕**, 但没有说出*铜钥匙*的来历。',
      '',
      '- 灯塔一层',
      '- 灯芯槽',
      '',
      '> 门轴在楼下转动。',
    ].join('\n')

    expect(novelDocumentToMarkdown(novelDocumentFromMarkdown(markdown))).toBe(
      markdown
    )
  })

  it('publishes Markdown when the Novel document changes', async () => {
    const onChange = vi.fn()
    render(
      <NovelManuscriptEditor
        initialContent={novelDocumentFromMarkdown('初始正文。')}
        onChange={onChange}
      />
    )
    const editor = screen
      .getByTestId('novel-manuscript-editor')
      .querySelector<HTMLElement>('.ProseMirror')
    expect(editor).not.toBeNull()

    if (!editor) return
    editor.innerHTML = '<p>改写后的正文。</p>'
    fireEvent.input(editor)

    await waitFor(() => {
      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ markdown: '改写后的正文。' })
      )
    })
  })
})
