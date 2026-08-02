import type { WorldBibleEntry } from './useWorldBible'

export function WorldEntryInspector({
  branchId,
  entry,
}: {
  branchId: string
  entry: WorldBibleEntry | null
}) {
  return (
    <aside className="border-t bg-muted/15 p-5 lg:border-l lg:border-t-0">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
        Provenance
      </p>
      {!entry ? (
        <p className="mt-4 text-sm text-muted-foreground">选择条目查看来源。</p>
      ) : (
        <dl className="mt-4 space-y-4 text-xs">
          <div>
            <dt className="text-muted-foreground">所属分支</dt>
            <dd className="mt-1 font-mono">{branchId}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">状态 / 置信度</dt>
            <dd className="mt-1">{entry.status} · {entry.confidence.toFixed(2)}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">来源 Step</dt>
            <dd className="mt-1 font-mono">{entry.first_seen_step}–{entry.last_updated_step}</dd>
          </div>
          <div>
            <dt className="text-muted-foreground">来源 Event</dt>
            <dd className="mt-1 space-y-1 break-all font-mono">
              {entry.source_event_ids.map((id) => <div key={id}>{id}</div>)}
              {entry.source_event_ids.length === 0 && 'Project Seed'}
            </dd>
          </div>
          <div>
            <dt className="text-muted-foreground">来源 Memory</dt>
            <dd className="mt-1 space-y-1 break-all font-mono">
              {entry.source_record_ids.map((id) => <div key={id}>{id}</div>)}
            </dd>
          </div>
        </dl>
      )}
    </aside>
  )
}
