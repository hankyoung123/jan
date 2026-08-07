import type { ManuscriptSourceCandidate } from './useManuscript'

export function ManuscriptSourcePicker({
  sources,
  selectedIds,
  onToggle,
}: {
  sources: ManuscriptSourceCandidate[]
  selectedIds: string[]
  onToggle: (source: ManuscriptSourceCandidate) => void
}) {
  return (
    <div className="divide-y border-y">
      {sources.map((source) => (
        <button
          className={`w-full px-1 py-4 text-left transition-colors ${
            selectedIds.includes(source.source_id)
              ? 'bg-amber-500/10'
              : 'hover:bg-muted/40'
          }`}
          key={source.source_id}
          onClick={() => onToggle(source)}
          type="button"
        >
          <span className="flex items-center justify-between gap-3">
            <strong className="line-clamp-1 text-sm font-medium">
              {source.title_hint}
            </strong>
            <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
              S{source.from_step}–S{source.to_step}
            </span>
          </span>
          <span className="mt-1 line-clamp-2 block text-xs leading-5 text-muted-foreground">
            {source.event_summary_text}
          </span>
          <span className="mt-2 block text-[10px] uppercase tracking-[0.15em] text-muted-foreground">
            {source.boundary} · {source.status}
          </span>
        </button>
      ))}
      {sources.length === 0 && (
        <p className="py-5 text-sm text-muted-foreground">
          当前分支还没有可写的模拟片段。
        </p>
      )}
    </div>
  )
}
