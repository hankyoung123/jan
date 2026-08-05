export type MessagePreset = 'chat' | 'live-agent' | 'readonly'

export interface MessageCapabilities {
  attachments: boolean
  edit: boolean
  delete: boolean
  regenerate: boolean
  continue: boolean
  versions: boolean
  citations: boolean
  tools: boolean
  reasoning: boolean
  copy: boolean
  timestamp: boolean
  metrics: boolean
}

export interface MessagePresentation {
  reasoningDefaultOpen: boolean
  reasoningCollapsedPreview: boolean
}

export interface StoryMessageMetadata {
  callId: string
  agentType: string
  agentName: string
  taskLabel: string
  sessionId?: string
  branchId?: string
  step?: number
  stage?: string
  model?: string
  duration?: number
  promptTokens?: number
  completionTokens?: number
  outputStatus?: 'streaming' | 'completed' | 'failed'
  error?: string
  createdAt?: Date | string
  stopped?: boolean
  versionGroupId?: string
  versionIndex?: number
  active?: boolean
}

type MessagePresetDefinition = {
  capabilities: MessageCapabilities
  presentation: MessagePresentation
}

export const MESSAGE_PRESETS: Record<MessagePreset, MessagePresetDefinition> = {
  chat: {
    capabilities: {
      attachments: true,
      edit: true,
      delete: true,
      regenerate: true,
      continue: true,
      versions: true,
      citations: true,
      tools: true,
      reasoning: true,
      copy: true,
      timestamp: true,
      metrics: true,
    },
    presentation: {
      reasoningDefaultOpen: true,
      reasoningCollapsedPreview: false,
    },
  },
  'live-agent': {
    capabilities: {
      attachments: false,
      edit: false,
      delete: false,
      regenerate: false,
      continue: false,
      versions: false,
      citations: false,
      tools: true,
      reasoning: true,
      copy: true,
      timestamp: false,
      metrics: true,
    },
    presentation: {
      reasoningDefaultOpen: false,
      reasoningCollapsedPreview: true,
    },
  },
  readonly: {
    capabilities: {
      attachments: true,
      edit: false,
      delete: false,
      regenerate: false,
      continue: false,
      versions: false,
      citations: true,
      tools: true,
      reasoning: true,
      copy: true,
      timestamp: true,
      metrics: true,
    },
    presentation: {
      reasoningDefaultOpen: true,
      reasoningCollapsedPreview: false,
    },
  },
}

export function resolveMessageCapabilities(
  preset: MessagePreset,
  overrides?: Partial<MessageCapabilities>
): MessageCapabilities {
  return { ...MESSAGE_PRESETS[preset].capabilities, ...overrides }
}
