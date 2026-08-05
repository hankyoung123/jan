import type { UIMessage } from 'ai'

import { MessageItem } from '@/containers/MessageItem'
import type {
  MessageCapabilities,
  MessagePreset,
  StoryMessageMetadata,
} from '@/lib/message-capabilities'
import { messageStatus } from '../modelMessages'

export function AgentMessageList({
  messages,
  preset = 'live-agent',
  capabilities,
}: {
  messages: UIMessage<StoryMessageMetadata>[]
  preset?: MessagePreset
  capabilities?: Partial<MessageCapabilities>
}) {
  if (messages.length === 0) return null
  return (
    <div className="space-y-4" data-testid="agent-message-list">
      {messages.map((message, index) => {
        const metadata = message.metadata
        const metrics = [
          metadata?.model,
          metadata?.duration === undefined
            ? undefined
            : `${metadata.duration.toFixed(1)}s`,
          metadata?.completionTokens
            ? `${metadata.completionTokens} tokens`
            : undefined,
        ].filter(Boolean)
        return (
          <article key={message.id}>
            <header className="mb-2 border-l-2 border-l-amber-500 pl-3">
              <p className="text-xs font-medium">
                {metadata?.agentName || metadata?.agentType || 'Agent'}
              </p>
              <p className="text-[10px] text-muted-foreground">
                {metadata?.taskLabel || metadata?.stage || 'Agent output'}
                {metadata?.step === undefined ? '' : ` · Step ${metadata.step}`}
              </p>
              {metrics.length > 0 && (
                <p className="mt-1 text-[10px] text-muted-foreground">
                  {metrics.join(' · ')}
                </p>
              )}
            </header>
            <MessageItem
              capabilities={capabilities}
              isFirstMessage={index === 0}
              isLastMessage={index === messages.length - 1}
              message={message}
              preset={preset}
              status={messageStatus(message)}
            />
          </article>
        )
      })}
    </div>
  )
}
