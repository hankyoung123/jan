import { useSyncExternalStore } from 'react'

const storageKey = 'story-engine.active-project-id'
const projectIdPattern = /^[a-z0-9][a-z0-9-]*$/
const projectChangedEvent = 'story-engine:active-project-changed'

function storage(): Storage | null {
  return typeof window === 'undefined' ? null : window.localStorage
}

export function getActiveStoryProjectId(): string | null {
  const projectId = storage()?.getItem(storageKey) ?? null
  return projectId && projectIdPattern.test(projectId) ? projectId : null
}

export function setActiveStoryProjectId(projectId: string): void {
  if (!projectIdPattern.test(projectId)) {
    throw new Error('invalid Story Engine project ID')
  }
  storage()?.setItem(storageKey, projectId)
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(projectChangedEvent))
  }
}

export function clearActiveStoryProject(): void {
  storage()?.removeItem(storageKey)
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new Event(projectChangedEvent))
  }
}

function subscribe(onChange: () => void) {
  window.addEventListener(projectChangedEvent, onChange)
  window.addEventListener('storage', onChange)
  return () => {
    window.removeEventListener(projectChangedEvent, onChange)
    window.removeEventListener('storage', onChange)
  }
}

export function useActiveStoryProjectId(): string | null {
  return useSyncExternalStore(subscribe, getActiveStoryProjectId, () => null)
}
