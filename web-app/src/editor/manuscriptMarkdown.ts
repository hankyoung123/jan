import type { JSONContent } from '@tiptap/core'

function inlineMarkdown(node: JSONContent): string {
  if (node.type === 'hardBreak') return '  \n'
  let value = node.text ?? ''
  for (const mark of [...(node.marks ?? [])].reverse()) {
    if (mark.type === 'bold') value = `**${value}**`
    if (mark.type === 'italic') value = `*${value}*`
    if (mark.type === 'code') value = `\`${value}\``
  }
  return value
}

function blockMarkdown(node: JSONContent, index = 1): string {
  const inline = () => (node.content ?? []).map(inlineMarkdown).join('')
  if (node.type === 'paragraph') return inline()
  if (node.type === 'heading') {
    const level = Math.min(3, Math.max(2, Number(node.attrs?.level ?? 2)))
    return `${'#'.repeat(level)} ${inline()}`
  }
  if (node.type === 'blockquote') {
    return (node.content ?? [])
      .map((child) => blockMarkdown(child))
      .join('\n\n')
      .split('\n')
      .map((line) => `> ${line}`)
      .join('\n')
  }
  if (node.type === 'bulletList' || node.type === 'orderedList') {
    return (node.content ?? [])
      .map((item, itemIndex) => {
        const prefix =
          node.type === 'orderedList' ? `${itemIndex + index}. ` : '- '
        const value = (item.content ?? [])
          .map((child) => blockMarkdown(child))
          .join('\n')
        return `${prefix}${value}`
      })
      .join('\n')
  }
  if (node.type === 'codeBlock') return `\`\`\`\n${inline()}\n\`\`\``
  if (node.type === 'horizontalRule') return '---'
  return (node.content ?? [])
    .map((child) => blockMarkdown(child))
    .join('\n')
}

export function novelDocumentToMarkdown(content: JSONContent): string {
  return (content.content ?? [])
    .map((node) => blockMarkdown(node))
    .join('\n\n')
    .trim()
}

function inlineContent(value: string): JSONContent[] {
  const nodes: JSONContent[] = []
  const pattern = /(\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`)/g
  let cursor = 0
  for (const match of value.matchAll(pattern)) {
    const position = match.index ?? 0
    if (position > cursor) {
      nodes.push({ type: 'text', text: value.slice(cursor, position) })
    }
    const text = match[2] ?? match[3] ?? match[4] ?? match[0]
    const mark = match[2] ? 'bold' : match[3] ? 'italic' : 'code'
    nodes.push({ type: 'text', text, marks: [{ type: mark }] })
    cursor = position + match[0].length
  }
  if (cursor < value.length) {
    nodes.push({ type: 'text', text: value.slice(cursor) })
  }
  return nodes.length ? nodes : [{ type: 'text', text: value }]
}

function paragraph(value: string): JSONContent {
  return { type: 'paragraph', content: inlineContent(value) }
}

export function novelDocumentFromMarkdown(markdown: string): JSONContent {
  const lines = markdown.replace(/\r\n?/g, '\n').split('\n')
  const content: JSONContent[] = []
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) {
      index += 1
      continue
    }
    const heading = /^(#{2,3})\s+(.+)$/.exec(line)
    if (heading) {
      content.push({
        type: 'heading',
        attrs: { level: heading[1].length },
        content: inlineContent(heading[2]),
      })
      index += 1
      continue
    }
    if (/^>\s?/.test(line)) {
      const quote: string[] = []
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quote.push(lines[index].replace(/^>\s?/, ''))
        index += 1
      }
      content.push({
        type: 'blockquote',
        content: [paragraph(quote.join('\n'))],
      })
      continue
    }
    const listType = /^-\s+/.test(line)
      ? 'bulletList'
      : /^\d+\.\s+/.test(line)
        ? 'orderedList'
        : null
    if (listType) {
      const items: JSONContent[] = []
      const itemPattern =
        listType === 'bulletList' ? /^-\s+(.+)$/ : /^\d+\.\s+(.+)$/
      while (index < lines.length) {
        const item = itemPattern.exec(lines[index])
        if (!item) break
        items.push({ type: 'listItem', content: [paragraph(item[1])] })
        index += 1
      }
      content.push({ type: listType, content: items })
      continue
    }
    const prose = [line]
    index += 1
    while (
      index < lines.length &&
      lines[index].trim() &&
      !/^(#{2,3})\s+|^>\s?|^-\s+|^\d+\.\s+/.test(lines[index])
    ) {
      prose.push(lines[index])
      index += 1
    }
    content.push(paragraph(prose.join('\n')))
  }
  return {
    type: 'doc',
    content: content.length ? content : [paragraph('')],
  }
}

