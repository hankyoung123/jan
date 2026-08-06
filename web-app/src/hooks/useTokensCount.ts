import { useMemo } from 'react'
import type { ThreadMessage } from '@janhq/core'
import { parseContextOverflow } from '@/utils/error'
import { useModelProvider } from './useModelProvider'

export interface TokenCountData {
  tokenCount: number
  inputTokens?: number
  outputTokens?: number
  maxTokens?: number
  percentage?: number
  isNearLimit: boolean
  loading: boolean
  modelDisplayName?: string
  fitEnabled: boolean
  configuredCtxLen?: number
  error?: string
  isOverflow?: boolean
}

interface UsageMeta {
  inputTokens?: number
  outputTokens?: number
  totalTokens?: number
}

const latestUsage = (messages: ThreadMessage[]): UsageMeta => {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const usage = (messages[index].metadata as { usage?: UsageMeta } | undefined)?.usage
    if (usage && typeof usage.totalTokens === 'number') return usage
  }
  return {}
}

const latestOverflow = (messages: ThreadMessage[]) => {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const value = (
      messages[index].metadata as { contextError?: unknown } | undefined
    )?.contextError
    if (typeof value === 'string' && value) return parseContextOverflow(value)
  }
  return null
}

export const useTokensCount = (messages: ThreadMessage[] = []) => {
  const selectedModel = useModelProvider((state) => state.selectedModel)
  const data = useMemo<TokenCountData>(() => {
    const usage = latestUsage(messages)
    const overflow = latestOverflow(messages)
    const tokenCount = overflow?.requestTokens ?? usage.totalTokens ?? 0
    const maxTokens = overflow?.contextTokens
    const percentage = maxTokens ? (tokenCount / maxTokens) * 100 : undefined
    return {
      tokenCount,
      inputTokens: overflow ? overflow.requestTokens : usage.inputTokens,
      outputTokens: overflow ? 0 : usage.outputTokens,
      maxTokens,
      percentage,
      isNearLimit: overflow !== null || (percentage ?? 0) > 85,
      loading: false,
      modelDisplayName: selectedModel?.name || selectedModel?.id,
      fitEnabled: false,
      isOverflow: overflow !== null,
    }
  }, [messages, selectedModel])

  return { ...data, calculateTokens: async () => undefined }
}
