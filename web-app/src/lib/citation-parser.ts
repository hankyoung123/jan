import type { CitationsPayload, WebCitation } from '@/components/Citations'

const tryParseJson = (s: string): unknown => {
  try {
    return JSON.parse(s)
  } catch {
    return null
  }
}

const isWebCitation = (x: unknown): x is WebCitation => {
  if (!x || typeof x !== 'object') return false
  const o = x as Record<string, unknown>
  return typeof o.url === 'string' && /^https?:\/\//.test(o.url)
}

const isTextItem = (it: unknown): it is { type: string; text: string } =>
  !!it &&
  typeof it === 'object' &&
  (it as { type?: string }).type === 'text' &&
  typeof (it as { text?: string }).text === 'string'

const extractTextItems = (output: unknown): string[] => {
  if (typeof output === 'string') return [output]
  if (Array.isArray(output)) {
    return output.filter(isTextItem).map((it) => it.text)
  }
  if (!output || typeof output !== 'object') return []
  const o = output as { content?: unknown }
  if (Array.isArray(o.content)) {
    return o.content.filter(isTextItem).map((it) => it.text)
  }
  return []
}

const fromWebPayload = (obj: unknown): CitationsPayload | null => {
  let arr: unknown[] | null = null
  let query: string | undefined

  if (Array.isArray(obj)) {
    arr = obj
  } else if (obj && typeof obj === 'object') {
    const o = obj as Record<string, unknown>
    if (typeof o.query === 'string') query = o.query
    for (const key of ['results', 'citations', 'sources', 'data']) {
      const v = o[key]
      if (Array.isArray(v)) {
        arr = v
        break
      }
    }
  }

  if (!arr) return null
  const filtered = arr.filter(isWebCitation)
  if (!filtered.length) return null
  return { kind: 'web', query, citations: filtered }
}

export function parseCitationsFromToolOutput(
  output: unknown
): CitationsPayload | null {
  const texts = extractTextItems(output)
  for (const text of texts) {
    const parsed = tryParseJson(text)
    if (parsed !== null) {
      const web = fromWebPayload(parsed)
      if (web) return web
    }
  }

  const web = fromWebPayload(output)
  if (web) return web

  return null
}
