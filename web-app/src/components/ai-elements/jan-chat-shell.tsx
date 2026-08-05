import type { ChatStatus, UIMessage } from 'ai'
import type { ReactNode } from 'react'
import { memo } from 'react'

import { PromptProgress } from '@/components/PromptProgress'
import { MessageItem } from '@/containers/MessageItem'
import { cn } from '@/lib/utils'
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from './conversation'

type JanChatShellProps = {
  messages: UIMessage[]
  status: ChatStatus
  composer: ReactNode
  title: string
  subtitle: string
  pendingLabel?: string
  className?: string
  getVersionInfo?: (
    message: UIMessage
  ) => { index: number; count: number } | undefined
  messageProps?: Pick<
    React.ComponentProps<typeof MessageItem>,
    | 'preset'
    | 'capabilities'
    | 'onRegenerate'
    | 'onContinue'
    | 'onEdit'
    | 'onDelete'
    | 'onSwitchVersion'
  >
}

/**
 * The presentation layer used by Jan's original thread page: sticky message
 * scrolling, Jan message rendering, progress feedback, and the fixed composer
 * slot. Domain state and transport deliberately stay outside this component.
 */
export const JanChatShell = memo(function JanChatShell({
  messages,
  status,
  composer,
  title,
  subtitle,
  pendingLabel,
  className,
  getVersionInfo,
  messageProps,
}: JanChatShellProps) {
  const pending = status === 'submitted' || status === 'streaming'

  return (
    <section
      aria-busy={pending}
      className={cn(
        'flex h-[620px] min-h-0 flex-col overflow-hidden border-b bg-background lg:h-[680px] lg:border-b-0 lg:border-r',
        className
      )}
      data-testid="jan-chat-shell"
    >
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-5">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-medium">{title}</h2>
          <p className="truncate text-xs text-muted-foreground">{subtitle}</p>
        </div>
        <span className="rounded-full border px-2.5 py-1 text-[11px] text-muted-foreground">
          投稿讨论
        </span>
      </header>

      <div className="relative min-h-0 flex-1">
        <Conversation className="absolute inset-0 text-start">
          <ConversationContent className="mx-auto w-full px-4 py-6 md:w-11/12 xl:w-5/6">
            {messages.map((message, index) => (
              <MessageItem
                isAnimating={!pending}
                isFirstMessage={index === 0}
                isLastMessage={index === messages.length - 1}
                key={message.id}
                message={message}
                status={status}
                versionInfo={getVersionInfo?.(message)}
                {...messageProps}
              />
            ))}
            {pending && (
              <div
                aria-live="polite"
                className="flex items-center gap-2 text-sm text-muted-foreground"
                role="status"
              >
                <PromptProgress />
                {pendingLabel && <span>{pendingLabel}</span>}
              </div>
            )}
          </ConversationContent>
          <ConversationScrollButton aria-label="滚动到最新消息" />
        </Conversation>
      </div>

      <div className="mx-auto w-full shrink-0 px-4 pb-4 pt-3 md:w-11/12 xl:w-5/6">
        {composer}
      </div>
    </section>
  )
})
