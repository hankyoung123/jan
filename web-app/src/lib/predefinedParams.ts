/** Sampling parameters supported by remote model APIs. */
export type ParamControllerType =
  | 'slider'
  | 'input'
  | 'checkbox'
  | 'dropdown'
  | 'textarea'

export type SamplerCap =
  | 'core'
  | 'penalties'
  | 'top_k'
  | 'min_p'
  | 'repetition'
  | 'json_schema'
  | 'thinking_budget'
  | 'client_only'

export interface ParamControllerProps {
  min?: number
  max?: number
  step?: number
  warnAbove?: number
  warnBelow?: number
  placeholder?: string
  rows?: number
  options?: Array<{ value: number | string; name: string }>
}

export interface ParamDef {
  key: string
  title: string
  description: string
  value: string | number | boolean
  controllerType: ParamControllerType
  controllerProps?: ParamControllerProps
  capability: SamplerCap
  disabledBy?: (values: Record<string, unknown>) => string | null
  effectHint?: string
}

const disabledAtZeroTemperature = (values: Record<string, unknown>) =>
  Number(values.temperature) === 0 ? 'Ignored when Temperature is 0' : null

export const paramsSettings: Record<string, ParamDef> = {
  stream: {
    key: 'stream', title: 'Stream', description: 'Enables real-time response streaming.',
    value: true, controllerType: 'checkbox', capability: 'core',
  },
  max_context_tokens: {
    key: 'max_context_tokens', title: 'Max Context Tokens',
    description: 'Total client-side input and output token budget. 0 disables trimming.',
    value: 0, controllerType: 'input', controllerProps: { min: 0, step: 1 },
    capability: 'client_only',
  },
  max_output_tokens: {
    key: 'max_output_tokens', title: 'Max Output Tokens',
    description: 'Maximum tokens the remote model may generate in one reply.',
    value: 2048, controllerType: 'input', controllerProps: { min: 0, step: 1 },
    capability: 'core',
  },
  auto_compact: {
    key: 'auto_compact', title: 'Auto Compact',
    description: 'Summarize older messages when the context budget is reached.',
    value: false, controllerType: 'checkbox', capability: 'client_only',
  },
  temperature: {
    key: 'temperature', title: 'Temperature', description: 'Controls response randomness.',
    value: 0.8, controllerType: 'slider',
    controllerProps: { min: 0, max: 2, step: 0.05, warnAbove: 1.5 },
    capability: 'core', effectHint: 'Higher = more varied; 0 = deterministic.',
  },
  top_p: {
    key: 'top_p', title: 'Top P', description: 'Nucleus sampling threshold.',
    value: 0.95, controllerType: 'slider',
    controllerProps: { min: 0, max: 1, step: 0.01 }, capability: 'core',
    disabledBy: disabledAtZeroTemperature,
  },
  top_k: {
    key: 'top_k', title: 'Top K', description: 'Sample from the top K likely tokens.',
    value: 40, controllerType: 'slider',
    controllerProps: { min: 0, max: 200, step: 1 }, capability: 'top_k',
    disabledBy: disabledAtZeroTemperature,
  },
  min_p: {
    key: 'min_p', title: 'Min P',
    description: 'Minimum relative probability for a token to be considered.',
    value: 0.05, controllerType: 'slider',
    controllerProps: { min: 0, max: 1, step: 0.01 }, capability: 'min_p',
  },
  frequency_penalty: {
    key: 'frequency_penalty', title: 'Frequency Penalty',
    description: 'Reduces repetition based on prior frequency.', value: 0,
    controllerType: 'slider', controllerProps: { min: -2, max: 2, step: 0.05 },
    capability: 'penalties',
  },
  presence_penalty: {
    key: 'presence_penalty', title: 'Presence Penalty',
    description: 'Encourages the model to introduce new topics.', value: 0,
    controllerType: 'slider', controllerProps: { min: -2, max: 2, step: 0.05 },
    capability: 'penalties',
  },
  repeat_penalty: {
    key: 'repeat_penalty', title: 'Repeat Penalty',
    description: 'Multiplicative repetition penalty supported by some remote APIs.',
    value: 1, controllerType: 'slider',
    controllerProps: { min: 1, max: 2, step: 0.01 }, capability: 'repetition',
  },
  json_schema: {
    key: 'json_schema', title: 'JSON Schema',
    description: 'Schema constraining supported remote APIs to valid JSON output.',
    value: '', controllerType: 'textarea', controllerProps: { rows: 4 },
    capability: 'json_schema',
  },
  thinking_budget_tokens: {
    key: 'thinking_budget_tokens', title: 'Thinking Budget',
    description: 'Maximum reasoning tokens for remote APIs that expose this control.',
    value: -1, controllerType: 'input', controllerProps: { min: -1, step: 1 },
    capability: 'thinking_budget',
  },
}

export const SAMPLER_DEFAULT_KEYS = [
  'temperature', 'top_p', 'top_k', 'min_p', 'repeat_penalty',
  'presence_penalty', 'frequency_penalty',
] as const

export function resolveSamplerValue(
  stored: unknown,
  fallback: string | number | boolean
): string | number | boolean {
  return stored === undefined || stored === null || stored === ''
    ? fallback
    : (stored as string | number | boolean)
}

export function evaluateDisabled(
  def: ParamDef,
  currentValues: Record<string, unknown>
): string | null {
  return def.disabledBy?.(currentValues) ?? null
}

export interface ParamGroup {
  id: string
  title: string
  description: string
  members: string[]
  triggerKey: string
  triggerValue: string | number | boolean
  capability: SamplerCap
}

export const paramGroups: ParamGroup[] = []

export interface ParamCategory {
  id: string
  title: string
  paramKeys: string[]
  groupIds: string[]
}

export const paramCategories: ParamCategory[] = [
  {
    id: 'generation',
    title: 'Generation',
    paramKeys: ['temperature', 'top_p', 'top_k', 'min_p', 'max_output_tokens'],
    groupIds: [],
  },
  {
    id: 'penalties',
    title: 'Penalties',
    paramKeys: ['frequency_penalty', 'presence_penalty', 'repeat_penalty'],
    groupIds: [],
  },
  {
    id: 'structured',
    title: 'Structured output',
    paramKeys: ['json_schema', 'thinking_budget_tokens'],
    groupIds: [],
  },
  {
    id: 'client',
    title: 'Client behavior',
    paramKeys: ['stream', 'max_context_tokens', 'auto_compact'],
    groupIds: [],
  },
]
