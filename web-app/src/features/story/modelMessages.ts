import type {
  EngineEventEnvelope,
  ModelMessageEventPayload,
} from '@story-engine/contracts'
import type { ChatStatus, UIMessage } from 'ai'

import type { StoryMessageMetadata } from '@/lib/message-capabilities'

export type StoryModelMessage = UIMessage<StoryMessageMetadata>
export type StoryModelMessageMap = Record<string, StoryModelMessage>

const MODEL_MESSAGE_EVENT_TYPES = new Set([
  'model.message.started',
  'model.message.delta',
  'model.message.completed',
  'model.message.failed',
])

export function isModelMessagePayload(
  value: unknown
): value is ModelMessageEventPayload {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<ModelMessageEventPayload>
  return (
    typeof candidate.message_id === 'string' &&
    candidate.role === 'assistant' &&
    typeof candidate.metadata === 'object' &&
    candidate.metadata !== null &&
    typeof candidate.metadata.call_id === 'string' &&
    typeof candidate.metadata.agent_type === 'string' &&
    typeof candidate.metadata.agent_name === 'string' &&
    typeof candidate.metadata.task_label === 'string'
  )
}

export function isModelMessageEvent(event: EngineEventEnvelope): boolean {
  return MODEL_MESSAGE_EVENT_TYPES.has(event.type)
}

function metadataFromEvent(
  payload: ModelMessageEventPayload,
  timestamp: string,
  outputStatus: StoryMessageMetadata['outputStatus'],
  current?: StoryMessageMetadata
): StoryMessageMetadata {
  const metadata = payload.metadata
  return {
    callId: metadata.call_id,
    agentType: metadata.agent_type,
    agentName: metadata.agent_name,
    taskLabel: metadata.task_label,
    sessionId: metadata.session_id ?? undefined,
    branchId: metadata.branch_id ?? undefined,
    step: metadata.step ?? undefined,
    stage: metadata.stage ?? undefined,
    stageEventId: metadata.stage_event_id ?? undefined,
    model: metadata.model ?? undefined,
    duration:
      metadata.duration_ms == null ? undefined : metadata.duration_ms / 1000,
    promptTokens: metadata.prompt_tokens,
    completionTokens: metadata.completion_tokens,
    reasoningTokens: metadata.reasoning_tokens ?? current?.reasoningTokens,
    retryCount: metadata.retry_count ?? current?.retryCount,
    inputMessages:
      payload.input_messages?.length
        ? payload.input_messages
        : current?.inputMessages,
    outputSchema: payload.output_schema ?? current?.outputSchema,
    outputStatus,
    error: payload.error ?? undefined,
    createdAt: current?.createdAt ?? timestamp,
  }
}

function mergeMessagePart(
  parts: UIMessage['parts'],
  payload: NonNullable<ModelMessageEventPayload['part']>
): UIMessage['parts'] {
  if (payload.type === 'text' || payload.type === 'reasoning') {
    const delta = payload.text_delta ?? ''
    if (!delta) return parts
    const last = parts.at(-1)
    if (last && last.type === payload.type && 'text' in last) {
      return [
        ...parts.slice(0, -1),
        { ...last, text: `${last.text}${delta}` },
      ] as UIMessage['parts']
    }
    return [...parts, { type: payload.type, text: delta }] as UIMessage['parts']
  }

  if (payload.type === 'file') {
    if (!payload.media_type || !payload.url) return parts
    return [
      ...parts,
      {
        type: 'file',
        mediaType: payload.media_type,
        url: payload.url,
        filename: payload.filename ?? undefined,
      },
    ] as UIMessage['parts']
  }

  if (payload.type.startsWith('tool-')) {
    const toolCallId = payload.tool_call_id ?? `${payload.type}:${parts.length}`
    const index = parts.findIndex(
      (part) =>
        part.type === payload.type &&
        (part as { toolCallId?: string }).toolCallId === toolCallId
    )
    const current = index < 0 ? undefined : parts[index]
    const toolPart = {
      ...(current ?? {}),
      type: payload.type,
      toolCallId,
      state:
        payload.state ?? (current as { state?: string } | undefined)?.state,
      ...(payload.input == null ? {} : { input: payload.input }),
      ...(payload.output == null ? {} : { output: payload.output }),
      ...(payload.error == null ? {} : { errorText: payload.error }),
    } as UIMessage['parts'][number]
    if (index < 0) return [...parts, toolPart]
    return parts.map((part, partIndex) =>
      partIndex === index ? toolPart : part
    ) as UIMessage['parts']
  }

  return parts
}

function completedParts(
  payload: ModelMessageEventPayload
): UIMessage['parts'] | undefined {
  if (!payload.parts?.length) return undefined
  return payload.parts.flatMap((part) => {
    if (part.type === 'text' || part.type === 'reasoning') {
      return part.text == null ? [] : [{ type: part.type, text: part.text }]
    }
    if (part.type === 'file') {
      return !part.media_type || !part.url
        ? []
        : [
            {
              type: 'file' as const,
              mediaType: part.media_type,
              url: part.url,
              filename: part.filename ?? undefined,
            },
          ]
    }
    if (part.type.startsWith('tool-') && part.state && part.tool_call_id) {
      return [
        {
          type: part.type,
          state: part.state,
          toolCallId: part.tool_call_id,
          input: part.input,
          output: part.output,
          errorText: part.error ?? undefined,
        } as UIMessage['parts'][number],
      ]
    }
    return []
  }) as UIMessage['parts']
}

export function reduceModelMessages(
  messages: StoryModelMessageMap,
  event: EngineEventEnvelope
): StoryModelMessageMap {
  if (!isModelMessageEvent(event) || !isModelMessagePayload(event.payload)) {
    return messages
  }
  const payload = event.payload
  const current = messages[payload.message_id]
  const outputStatus =
    event.type === 'model.message.failed'
      ? 'failed'
      : event.type === 'model.message.completed'
        ? 'completed'
        : 'streaming'
  const terminalParts = completedParts(payload)
  const parts = terminalParts
    ? terminalParts
    : event.type === 'model.message.delta' && payload.part
      ? mergeMessagePart(payload.reset ? [] : (current?.parts ?? []), payload.part)
      : payload.reset
        ? []
        : current?.parts ?? []
  return {
    ...messages,
    [payload.message_id]: {
      id: payload.message_id,
      role: 'assistant',
      parts,
      metadata: metadataFromEvent(
        payload,
        event.timestamp,
        outputStatus,
        current?.metadata
      ),
    },
  }
}

export function messageStatus(message: StoryModelMessage): ChatStatus {
  if (message.metadata?.outputStatus === 'failed') return 'error'
  if (message.metadata?.outputStatus === 'streaming') return 'streaming'
  return 'ready'
}
