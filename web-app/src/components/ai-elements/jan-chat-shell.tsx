import type { ChatStatus, UIMessage } from 'ai'
import { ArrowRight } from 'lucide-react'
import type { FormEvent, ReactNode } from 'react'
import { memo, useState } from 'react'
import TextareaAutosize from 'react-textarea-autosize'
import { IconLoader2 } from '@tabler/icons-react'

import { Button } from '@/components/ui/button'
import { PromptProgress } from '@/components/PromptProgress'
import { MessageItem } from '@/containers/MessageItem'
import { MovingBorder } from '@/containers/MovingBorder'
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
                hideActions
                isAnimating={!pending}
                isFirstMessage={index === 0}
                isLastMessage={index === messages.length - 1}
                key={message.id}
                message={message}
                status={status}
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

type JanChatComposerProps = {
  value: string
  onValueChange: (value: string) => void
  onSubmit: () => void
  placeholder: string
  ariaLabel: string
  disabled?: boolean
  busy?: boolean
  spellCheck?: boolean
  footer?: ReactNode
  autoFocus?: boolean
  className?: string
}

/**
 * Controlled adaptation of Jan's original ChatInput surface. It keeps Jan's
 * composer layout and keyboard behavior without importing thread, provider,
 * attachment, or inference state into another product domain.
 */
export const JanChatComposer = memo(function JanChatComposer({
  value,
  onValueChange,
  onSubmit,
  placeholder,
  ariaLabel,
  disabled = false,
  busy = false,
  spellCheck = true,
  footer,
  autoFocus = false,
  className,
}: JanChatComposerProps) {
  const [isFocused, setIsFocused] = useState(false)
  const canSubmit = value.trim().length > 0 && !disabled && !busy

  function submit(event?: FormEvent) {
    event?.preventDefault()
    if (canSubmit) onSubmit()
  }

  return (
    <form className={cn('relative', className)} onSubmit={submit}>
      <div className="relative overflow-hidden rounded-3xl p-0.5">
        {busy && (
          <div className="absolute inset-0">
            <MovingBorder rx="10%" ry="10%">
              <div className="h-100 w-100 bg-[radial-gradient(var(--app-primary),transparent_60%)]" />
            </MovingBorder>
          </div>
        )}
        <div
          className={cn(
            'relative z-20 rounded-3xl border border-input bg-white px-0 pb-10 dark:bg-input/30',
            isFocused && 'ring-1 ring-ring/50',
            disabled && 'opacity-70'
          )}
          data-jan-chat-composer="true"
        >
          <TextareaAutosize
            aria-label={ariaLabel}
            autoFocus={autoFocus}
            className="scrollbar-hide w-full shrink-0 resize-none border-none bg-transparent px-4 pt-4 outline-0"
            data-gramm={spellCheck}
            data-gramm_editor={spellCheck}
            data-gramm_grammarly={spellCheck}
            data-testid="chat-input"
            dir="auto"
            disabled={disabled}
            maxRows={10}
            minRows={2}
            onBlur={() => setIsFocused(false)}
            onChange={(event) => onValueChange(event.target.value)}
            onFocus={() => setIsFocused(true)}
            onKeyDown={(event) => {
              const isComposing =
                event.nativeEvent.isComposing || event.keyCode === 229
              if (event.key === 'Enter' && !event.shiftKey && !isComposing) {
                event.preventDefault()
                if (canSubmit) onSubmit()
              }
            }}
            placeholder={placeholder}
            spellCheck={spellCheck}
            value={value}
          />
        </div>
      </div>

      <div className="absolute bottom-0 z-20 w-full bg-transparent p-2">
        <div className="flex w-full items-center justify-between gap-3">
          <div className="min-w-0 flex-1 px-2 text-[11px] text-muted-foreground">
            {footer}
          </div>
          <Button
            aria-label={busy ? '正在整理设定包' : '发送消息'}
            className="mb-1 mr-1 rounded-full"
            data-test-id="send-message-button"
            disabled={!canSubmit}
            size="icon-sm"
            type="submit"
            variant={busy ? 'secondary' : 'default'}
          >
            {busy ? (
              <IconLoader2 className="animate-spin" />
            ) : (
              <ArrowRight className="text-primary-fg" />
            )}
          </Button>
        </div>
      </div>
    </form>
  )
})
