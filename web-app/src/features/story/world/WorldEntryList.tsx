import type { WorldBibleEntry } from './useWorldBible'

export function WorldEntryList({
  entries,
  selectedId,
  onSelect,
}: {
  entries: WorldBibleEntry[]
  selectedId?: string
  onSelect: (entry: WorldBibleEntry) => void
}) {
  if (entries.length === 0) {
    return <p className="text-sm text-muted-foreground">此分支暂无条目。</p>
  }
  return (
    <div className="divide-y border-y">
      {entries.map((entry) => (
        <button
          className={`grid w-full gap-2 px-1 py-4 text-left transition-colors sm:grid-cols-[1fr_auto] ${
            selectedId === entry.entry_id ? 'bg-amber-500/10' : 'hover:bg-muted/40'
          }`}
          key={entry.entry_id}
          onClick={() => onSelect(entry)}
          type="button"
        >
          <span>
            <strong className="block text-sm font-medium">{entry.title}</strong>
            <span className="mt-1 line-clamp-2 block text-sm leading-6 text-muted-foreground">
              {entry.content_text}
            </span>
          </span>
          <span className="font-mono text-[11px] text-muted-foreground">
            S{entry.first_seen_step} → S{entry.last_updated_step}
          </span>
        </button>
      ))}
    </div>
  )
}
