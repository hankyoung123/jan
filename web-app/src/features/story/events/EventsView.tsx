import type { components } from '@story-engine/contracts'
import { ListTree, Map as MapIcon } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { useActiveStoryProjectId } from '../activeProject'
import {
  PageHeader,
  StatusPill,
  StoryPage,
  StoryViewToggle,
} from '../components/StoryLayout'
import { engineRequest } from '../engine'

type StoryEvent = components['schemas']['StoryEvent']

type EventViewMode = 'timeline' | 'story-map'

function formatEventTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleTimeString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function eventParticipants(event: StoryEvent): string {
  return event.participants.join(' · ') || '未记录参与者'
}

function isEventTurningPoint(event: StoryEvent): boolean {
  return event.world_changes.length > 0 || event.character_changes.length > 0
}

function EventTimeline({ events }: { events: StoryEvent[] }) {
  return (
    <div className="max-w-4xl">
      {events.map((event, index) => (
        <article
          className="relative grid grid-cols-[48px_1fr] gap-4 pb-7"
          key={event.id}
        >
          {index < events.length - 1 && (
            <span className="absolute bottom-0 left-5 top-10 w-px bg-border" />
          )}
          <span className="z-10 grid size-10 place-items-center rounded-full border bg-background font-studio text-xs">
            {String(event.sequence).padStart(3, '0')}
          </span>
          <div className="border-b pb-6">
            <div className="mb-2 flex items-center gap-3 text-xs text-muted-foreground">
              <span>{formatEventTime(event.occurred_at)}</span>
              {event.approved_by_user ? (
                <StatusPill tone="success">已确认</StatusPill>
              ) : (
                <StatusPill tone="warning">待确认</StatusPill>
              )}
            </div>
            <h2 className="font-medium">{event.summary}</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              {eventParticipants(event)}
            </p>
          </div>
        </article>
      ))}
    </div>
  )
}

function StoryMapView({ events }: { events: StoryEvent[] }) {
  return (
    <div className="overflow-x-auto pb-4">
      <div className="flex min-w-max items-stretch gap-3">
        {events.map((event, index) => {
          const turningPoint = isEventTurningPoint(event)
          return (
            <div className="flex items-stretch gap-3" key={event.id}>
              {index > 0 && (
                <span className="mt-8 h-px w-8 shrink-0 bg-border" />
              )}
              <article
                className={`w-64 border bg-background ${
                  turningPoint ? 'border-primary/40' : ''
                }`}
              >
                <div className="flex items-center justify-between border-b px-4 py-3">
                  <span className="font-studio text-xs">
                    {String(event.sequence).padStart(3, '0')}
                  </span>
                  {event.approved_by_user ? (
                    <StatusPill tone="success">已确认</StatusPill>
                  ) : (
                    <StatusPill tone="warning">待确认</StatusPill>
                  )}
                </div>
                <div className="p-4">
                  <p className="text-xs text-muted-foreground">
                    {formatEventTime(event.occurred_at)}
                  </p>
                  <h3 className="mt-2 text-sm font-medium leading-6">
                    {event.summary}
                  </h3>
                  <p className="mt-3 text-xs text-muted-foreground">
                    {eventParticipants(event)}
                  </p>
                  <div className="mt-4 flex flex-wrap gap-2">
                    {turningPoint && <StatusPill>转折点</StatusPill>}
                    {event.fact_ids.length > 0 && (
                      <StatusPill>事实变更</StatusPill>
                    )}
                  </div>
                </div>
              </article>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function EventsView() {
  const projectId = useActiveStoryProjectId()
  const [events, setEvents] = useState<StoryEvent[]>([])
  const [mode, setMode] = useState<EventViewMode>('timeline')
  const [loading, setLoading] = useState(projectId !== null)
  const [error, setError] = useState<string | null>(null)

  const loadEvents = useCallback(async () => {
    if (!projectId) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const loaded = await engineRequest<StoryEvent[]>(
        `/projects/${projectId}/events`
      )
      setEvents(loaded)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '事件历史加载失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void loadEvents()
  }, [loadEvents])

  const orderedEvents = useMemo(
    () => [...events].sort((left, right) => left.sequence - right.sequence),
    [events]
  )
  const confirmedCount = orderedEvents.filter(
    (event) => event.approved_by_user
  ).length

  return (
    <StoryPage>
      <PageHeader
        action={
          projectId ? (
            <StoryViewToggle
              label="事件视图"
              onChange={setMode}
              options={[
                { value: 'timeline', label: '时间线', icon: ListTree },
                { value: 'story-map', label: '故事地图', icon: MapIcon },
              ]}
              value={mode}
            />
          ) : undefined
        }
        eyebrow={
          projectId
            ? `${confirmedCount} 个已确认事件`
            : '尚未选择项目'
        }
        title="事件历史"
      />
      {!projectId ? (
        <div className="rounded-md border bg-background p-8 text-center text-sm text-muted-foreground">
          先通过投稿讨论创建项目，再查看事件历史。
        </div>
      ) : loading ? (
        <p className="text-sm text-muted-foreground">正在读取事件…</p>
      ) : error ? (
        <div
          className="border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive"
          role="alert"
        >
          {error}
        </div>
      ) : orderedEvents.length === 0 ? (
        <div className="rounded-md border bg-background p-8 text-center text-sm text-muted-foreground">
          还没有已确认事件。
        </div>
      ) : mode === 'timeline' ? (
        <EventTimeline events={orderedEvents} />
      ) : (
        <StoryMapView events={orderedEvents} />
      )}
    </StoryPage>
  )
}

