export type ThinkingBudgetLevelKey =
  | 'low'
  | 'medium'
  | 'high'
  | 'xhigh'
  | 'unlimited'

// Fractions of the configured context window; null means unlimited.
export const THINKING_BUDGET_LEVELS: Array<{
  key: ThinkingBudgetLevelKey
  label: string
  ratio: number | null
}> = [
  { key: 'low', label: 'Low', ratio: 0.1 },
  { key: 'medium', label: 'Medium', ratio: 0.25 },
  { key: 'high', label: 'High', ratio: 0.5 },
  { key: 'xhigh', label: 'XHigh', ratio: 0.75 },
  { key: 'unlimited', label: 'Unlimited', ratio: null },
]

export const DEFAULT_THINKING_BUDGET_LEVEL: ThinkingBudgetLevelKey = 'unlimited'

export function tokensForThinkingBudgetLevel(
  level: ThinkingBudgetLevelKey,
  contextSize: number
): number {
  const ratio = THINKING_BUDGET_LEVELS.find((l) => l.key === level)?.ratio
  return ratio == null ? -1 : Math.max(1, Math.round(contextSize * ratio))
}

export function isThinkingBudgetLevelKey(
  value: unknown
): value is ThinkingBudgetLevelKey {
  return (
    typeof value === 'string' &&
    THINKING_BUDGET_LEVELS.some((l) => l.key === value)
  )
}
