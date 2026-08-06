import type { EngineEventEnvelope } from '@story-engine/contracts'
import { useEffect, useMemo, useState } from 'react'

import { engineRequest, subscribeProjectEvents } from './engine'
import {
  reduceModelMessages,
  type StoryModelMessageMap,
} from './modelMessages'

export function useProjectModelMessages(projectId?: string) {
  const [messagesById, setMessagesById] = useState<StoryModelMessageMap>({})
  const [revision, setRevision] = useState(0)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setMessagesById({})
    setError(null)
    if (!projectId) return
    const activeProjectId = projectId

    let disposed = false
    let unsubscribe: (() => void) | undefined

    const applyEvent = (event: EngineEventEnvelope) => {
      if (event.type === 'stream.resync_required') {
        setRevision((current) => current + 1)
        return
      }
      setMessagesById((current) => reduceModelMessages(current, event))
    }

    async function connect() {
      let restored: StoryModelMessageMap = {}
      let lastSequence = 0
      try {
        const history = await engineRequest<EngineEventEnvelope[]>(
          `/projects/${activeProjectId}/simulation-events?after_sequence=0`
        )
        for (const event of history) {
          restored = reduceModelMessages(restored, event)
          lastSequence = Math.max(lastSequence, event.sequence)
        }
      } catch {
        // The bounded event window is optional; the live stream can start empty.
      }
      if (disposed) return
      setMessagesById(restored)
      const stop = await subscribeProjectEvents(
        activeProjectId,
        applyEvent,
        lastSequence
      )
      if (disposed) stop()
      else unsubscribe = stop
    }

    void connect().catch((cause) => {
      if (!disposed) {
        setError(
          cause instanceof Error ? cause.message : 'Agent 消息流连接失败'
        )
      }
    })
    return () => {
      disposed = true
      unsubscribe?.()
    }
  }, [projectId, revision])

  const messages = useMemo(
    () =>
      Object.values(messagesById).sort((left, right) => {
        const leftTime = Date.parse(String(left.metadata?.createdAt ?? '')) || 0
        const rightTime = Date.parse(String(right.metadata?.createdAt ?? '')) || 0
        return leftTime - rightTime || left.id.localeCompare(right.id)
      }),
    [messagesById]
  )

  return { messages, error }
}
