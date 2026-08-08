import { SendHorizontal } from 'lucide-react'
import { type FormEvent, useState } from 'react'

type IntentInputProps = {
  disabled: boolean
  onSubmit: (text: string) => void
}

export function IntentInput({ disabled, onSubmit }: IntentInputProps) {
  const [text, setText] = useState('')

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const intent = text.trim()
    if (!intent || disabled) return
    onSubmit(intent)
    setText('')
  }

  return (
    <form className="border-t pt-4" onSubmit={submit}>
      <div className="flex items-end gap-2 border bg-background p-2">
        <textarea
          aria-label="你的意图"
          className="min-h-12 flex-1 resize-none bg-transparent px-2 py-1.5 text-base outline-none placeholder:text-muted-foreground"
          disabled={disabled}
          onChange={(event) => setText(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.metaKey || event.ctrlKey)) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
          placeholder="说出你想做的事…"
          value={text}
        />
        <button
          aria-label="提交意图"
          className="grid size-10 shrink-0 place-items-center bg-primary text-primary-foreground transition hover:brightness-95 disabled:cursor-not-allowed disabled:opacity-50"
          disabled={disabled || !text.trim()}
          title="提交意图"
          type="submit"
        >
          <SendHorizontal size={18} />
        </button>
      </div>
    </form>
  )
}
