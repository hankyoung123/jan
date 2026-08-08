import { GitFork, MapPin, Timer } from 'lucide-react'

export type TimelineEntry = {
  checkpoint_id: string
  step: number
  world_time: string
  is_current: boolean
}

type TimelineProps = {
  entries: TimelineEntry[]
  onFork: (entry: TimelineEntry) => void
  pending: boolean
}

export function Timeline({ entries, onFork, pending }: TimelineProps) {
  if (entries.length === 0) return null

  return (
    <section aria-label="世界时间线" className="border-b py-4">
      <div className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
        <MapPin size={14} />
        <span>时间线</span>
      </div>
      <ol className="flex gap-2 overflow-x-auto pb-1">
        {entries.map((entry) => (
          <li
            className={entry.is_current ? 'border-l-2 border-foreground pl-2' : 'border-l pl-2'}
            key={entry.checkpoint_id}
          >
            <div className="flex min-w-24 items-center justify-between gap-2">
              <div className="whitespace-nowrap text-xs">
                <span className="block font-medium">Step {entry.step}</span>
                <span className="mt-1 flex items-center gap-1 text-muted-foreground"><Timer size={12} />{entry.world_time}</span>
              </div>
              {!entry.is_current && (
                <button
                  aria-label={`从 Step ${entry.step} 创建分支`}
                  className="grid size-7 shrink-0 place-items-center border bg-background hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
                  disabled={pending}
                  onClick={() => onFork(entry)}
                  title={`从 Step ${entry.step} 创建分支`}
                  type="button"
                >
                  <GitFork size={14} />
                </button>
              )}
            </div>
          </li>
        ))}
      </ol>
    </section>
  )
}
