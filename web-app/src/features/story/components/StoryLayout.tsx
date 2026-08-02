import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'

export const primaryButton =
  'inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground shadow-sm transition hover:brightness-95 disabled:pointer-events-none disabled:opacity-50'
export const secondaryButton =
  'inline-flex h-9 items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium transition hover:bg-accent disabled:pointer-events-none disabled:opacity-50'

export function StoryPage({ children }: { children: ReactNode }) {
  return (
    <main className="h-svh overflow-y-auto bg-neutral-50 px-5 pb-12 pt-14 dark:bg-background md:px-8">
      <div className="mx-auto w-full max-w-6xl">{children}</div>
    </main>
  )
}

export function PageHeader({
  eyebrow,
  title,
  action,
}: {
  eyebrow: string
  title: string
  action?: ReactNode
}) {
  return (
    <header className="mb-6 flex min-h-14 flex-wrap items-end justify-between gap-4 border-b pb-5">
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">
          {eyebrow}
        </p>
        <h1 className="font-studio text-2xl font-medium">{title}</h1>
      </div>
      {action}
    </header>
  )
}

export function StatusPill({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'success' | 'warning' | 'danger' | 'neutral'
}) {
  const tones = {
    success: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
    warning: 'bg-amber-500/12 text-amber-700 dark:text-amber-300',
    danger: 'bg-destructive/10 text-destructive',
    neutral: 'bg-muted text-muted-foreground',
  }
  return (
    <span
      className={`inline-flex rounded px-2 py-1 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  )
}

export function StoryViewToggle<T extends string>({
  label,
  value,
  onChange,
  options,
}: {
  label: string
  value: T
  onChange: (value: T) => void
  options: Array<{ value: T; label: string; icon: LucideIcon }>
}) {
  return (
    <div
      aria-label={label}
      className="flex rounded-md border bg-background p-0.5"
      role="group"
    >
      {options.map((option) => {
        const Icon = option.icon
        return (
          <button
            aria-pressed={value === option.value}
            className={`inline-flex h-8 items-center gap-2 rounded px-3 text-sm ${
              value === option.value
                ? 'bg-accent font-medium'
                : 'text-muted-foreground hover:bg-accent/60'
            }`}
            key={option.value}
            onClick={() => onChange(option.value)}
            type="button"
          >
            <Icon size={15} />
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
