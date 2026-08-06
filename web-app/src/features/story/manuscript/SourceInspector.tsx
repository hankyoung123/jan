import type { UIMessage } from 'ai'

import type { StoryMessageMetadata } from '@/lib/message-capabilities'
import { AgentMessageList } from '../components/AgentMessageList'
import type { SceneDraft } from './useManuscript'

function reviewMessage(
  scene: SceneDraft
): UIMessage<StoryMessageMetadata> | null {
  if (!scene.review) return null
  const unsupported = scene.review.unsupported_facts.map(
    (fact) => `- 未支持事实：${fact}`
  )
  return {
    id: `review:${scene.id}:${scene.revision}`,
    role: 'assistant',
    parts: [
      {
        type: 'text',
        text: [scene.review.review.summary, ...unsupported].join('\n\n'),
      },
    ],
    metadata: {
      callId: `review:${scene.id}:${scene.revision}`,
      agentType: 'editor',
      agentName: 'Editor',
      taskLabel: '正文审校',
      branchId: scene.branch_id,
      step: scene.source_to_step,
      stage: 'editor',
      outputStatus: 'completed',
    },
  }
}

export function SourceInspector({
  scene,
  showReview = true,
  activityMessages = [],
}: {
  scene: SceneDraft | null
  showReview?: boolean
  activityMessages?: UIMessage<StoryMessageMetadata>[]
}) {
  const review = scene ? reviewMessage(scene) : null
  return (
    <aside className="border-t bg-muted/15 p-5 lg:border-l lg:border-t-0">
      <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
        Source Lineage
      </p>
      {!scene ? (
        <p className="mt-4 text-sm text-muted-foreground">选择场景查看来源。</p>
      ) : (
        <div className="mt-4 space-y-5 text-xs">
          <section>
            <p className="text-muted-foreground">Branch / Checkpoint</p>
            <p className="mt-1 break-all font-mono">{scene.branch_id}</p>
            <p className="mt-1 break-all font-mono text-[10px]">
              {scene.source_checkpoint_id}
            </p>
          </section>
          <section>
            <p className="text-muted-foreground">Step Range / Viewpoint</p>
            <p className="mt-1 font-mono">
              {scene.source_from_step}–{scene.source_to_step}
            </p>
            <p className="mt-1">
              {scene.viewpoint_actor_id || '自动选择主视角'}
            </p>
          </section>
          <section>
            <p className="text-muted-foreground">Resolved Events</p>
            <div className="mt-2 space-y-1 break-all font-mono text-[10px]">
              {scene.source_event_ids.map((id) => (
                <div key={id}>{id}</div>
              ))}
            </div>
          </section>
          <section>
            <p className="text-muted-foreground">Memory Records</p>
            <div className="mt-2 max-h-56 space-y-1 overflow-y-auto break-all font-mono text-[10px]">
              {scene.source_memory_ids.map((id) => (
                <div key={id}>{id}</div>
              ))}
            </div>
          </section>
          {showReview && review && (
            <section className="border-t pt-4">
              <p className="mb-3 text-muted-foreground">Editor Review</p>
              <AgentMessageList
                capabilities={{ metrics: false, timestamp: false }}
                messages={[review]}
                preset="readonly"
              />
            </section>
          )}
          {activityMessages.length > 0 && (
            <section className="border-t pt-4">
              <p className="mb-3 text-muted-foreground">Agent Activity</p>
              <AgentMessageList
                messages={activityMessages}
                preset="live-agent"
              />
            </section>
          )}
        </div>
      )}
    </aside>
  )
}
