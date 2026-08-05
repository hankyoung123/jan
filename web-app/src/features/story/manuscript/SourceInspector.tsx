import type { SceneDraft } from './useManuscript'

export function SourceInspector({ scene, showReview = true }: { scene: SceneDraft | null; showReview?: boolean }) {
  return (
    <aside className="border-t bg-muted/15 p-5 lg:border-l lg:border-t-0">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">Source Lineage</p>
      {!scene ? (
        <p className="mt-4 text-sm text-muted-foreground">选择场景查看来源。</p>
      ) : (
        <div className="mt-4 space-y-5 text-xs">
          <section>
            <p className="text-muted-foreground">Branch / Checkpoint</p>
            <p className="mt-1 break-all font-mono">{scene.branch_id}</p>
            <p className="mt-1 break-all font-mono text-[10px]">{scene.source_checkpoint_id}</p>
          </section>
          <section>
            <p className="text-muted-foreground">Step Range / Viewpoint</p>
            <p className="mt-1 font-mono">{scene.source_from_step}–{scene.source_to_step}</p>
            <p className="mt-1">{scene.viewpoint_actor_id || '自动选择主视角'}</p>
          </section>
          <section>
            <p className="text-muted-foreground">Resolved Events</p>
            <div className="mt-2 space-y-1 break-all font-mono text-[10px]">{scene.source_event_ids.map((id) => <div key={id}>{id}</div>)}</div>
          </section>
          <section>
            <p className="text-muted-foreground">Memory Records</p>
            <div className="mt-2 max-h-56 space-y-1 overflow-y-auto break-all font-mono text-[10px]">{scene.source_memory_ids.map((id) => <div key={id}>{id}</div>)}</div>
          </section>
          {showReview && scene.review && (
            <section className="border-t pt-4">
              <p className="text-muted-foreground">Editor Review</p>
              <p className="mt-2 leading-5">{scene.review.review.summary}</p>
              {scene.review.unsupported_facts.map((fact) => <p className="mt-2 border-l-2 border-amber-500 pl-2 text-amber-700" key={fact}>{fact}</p>)}
            </section>
          )}
        </div>
      )}
    </aside>
  )
}
