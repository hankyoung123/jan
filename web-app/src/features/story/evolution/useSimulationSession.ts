import type { components } from '@story-engine/contracts'
import { useCallback, useEffect, useState } from 'react'

import { engineRequest } from '../engine'

export type ProjectSnapshot = components['schemas']['ProjectSnapshot']
export type SessionSnapshot = components['schemas']['TurnSessionSnapshot']
export type SessionManifest = components['schemas']['SessionManifest']
export type StepResult = components['schemas']['StepResult']
export type CommitResult = components['schemas']['CommitResult']
export type ControlMode = components['schemas']['ControlMode']
export type BranchManifest = components['schemas']['BranchManifest']
export type BranchComparison = components['schemas']['BranchComparisonResponse']
type SimulationStartRequest = components['schemas']['SimulationStartRequest']

type Operation =
  | 'load'
  | 'start'
  | 'step'
  | 'run'
  | 'pause'
  | 'resume'
  | 'terminate'
  | 'cancel'
  | 'checkpoint'
  | 'restore'
  | 'fork'
  | 'locale'
  | 'compare'
  | 'maintenance'

interface StartOptions {
  branchId: string
  premise: string
  actorIds: string[]
  contentLocale: string
  mode: ControlMode
}

export function useSimulationSession(projectId?: string) {
  const [project, setProject] = useState<ProjectSnapshot | null>(null)
  const [session, setSession] = useState<SessionSnapshot | null>(null)
  const [lastStep, setLastStep] = useState<StepResult | null>(null)
  const [branches, setBranches] = useState<BranchManifest[]>([])
  const [sessions, setSessions] = useState<SessionManifest[]>([])
  const [pending, setPending] = useState<Set<Operation>>(new Set())
  const [error, setError] = useState<string | null>(null)

  const perform = useCallback(
    async <T,>(operation: Operation, request: () => Promise<T>): Promise<T | null> => {
      setPending((current) => new Set(current).add(operation))
      setError(null)
      try {
        return await request()
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'Story Engine request failed')
        return null
      } finally {
        setPending((current) => {
          const next = new Set(current)
          next.delete(operation)
          return next
        })
      }
    },
    []
  )

  const sessionId = session?.session_id
  const syncUrl = useCallback((next: SessionSnapshot | null, branchId?: string) => {
    if (typeof window === 'undefined') return
    const url = new URL(window.location.href)
    const selectedBranch = next?.branch_id || branchId
    if (selectedBranch) url.searchParams.set('branch', selectedBranch)
    if (next) url.searchParams.set('session', next.session_id)
    else url.searchParams.delete('session')
    window.history.replaceState({}, '', url)
  }, [])
  const refreshSession = useCallback(async () => {
    if (!projectId || !sessionId) return
    const updated = await engineRequest<SessionSnapshot>(
      `/projects/${projectId}/simulations/${sessionId}`
    )
    setSession(updated)
    syncUrl(updated)
  }, [projectId, sessionId, syncUrl])

  const load = useCallback(async () => {
    if (!projectId) {
      setProject(null)
      setSession(null)
      return
    }
    const loaded = await perform('load', async () => {
      const [projectSnapshot, sessions, loadedBranches] = await Promise.all([
        engineRequest<ProjectSnapshot>(`/projects/${projectId}`),
        engineRequest<SessionManifest[]>(`/projects/${projectId}/simulations`),
        engineRequest<BranchManifest[]>(`/projects/${projectId}/branches`).catch(
          () => []
        ),
      ])
      return { projectSnapshot, sessions, loadedBranches }
    })
    if (!loaded) return
    setProject(loaded.projectSnapshot)
    setBranches(loaded.loadedBranches)
    setSessions(loaded.sessions)
    const params = new URLSearchParams(window.location.search)
    const urlSessionId = params.get('session')
    const urlBranchId = params.get('branch')
    const ordered = [...loaded.sessions].sort((left, right) =>
      right.updated_at.localeCompare(left.updated_at)
    )
    const selectedManifest =
      ordered.find((item) => item.session_id === urlSessionId) ??
      ordered.find((item) => item.branch_id === urlBranchId) ??
      ordered[0]
    if (selectedManifest) {
      const restored = await engineRequest<SessionSnapshot | SessionManifest>(
        `/projects/${projectId}/simulations/${selectedManifest.session_id}`
      )
      if ('request' in restored) {
        setSession(restored)
        syncUrl(restored)
      } else {
        setSession(null)
        syncUrl(null, restored.branch_id)
        setError(restored.restoration_notice_text || '此会话没有可恢复的检查点')
      }
      return
    }
    if (loaded.sessions.length === 0) {
      try {
        const selectedBranch = urlBranchId || 'main'
        const branch = await engineRequest<components['schemas']['BranchManifest']>(
          `/projects/${projectId}/branches/${selectedBranch}`
        )
        if (branch.head_checkpoint_id) {
          const restored = await engineRequest<SessionSnapshot>(
            `/projects/${projectId}/simulations/restore`,
            {
              method: 'POST',
              body: JSON.stringify({ checkpoint_id: branch.head_checkpoint_id }),
            }
          )
          setSession(restored)
          syncUrl(restored)
          return
        }
      } catch {
        // A new project legitimately has neither a branch nor a checkpoint.
      }
    }
    setSession(null)
    syncUrl(null, urlBranchId || 'main')
  }, [perform, projectId, syncUrl])

  useEffect(() => {
    void load()
  }, [load])

  const start = useCallback(
    async (options: StartOptions) => {
      if (!projectId) return null
      const request = {
        branch_id: options.branchId,
        premise_text: options.premise,
        actor_ids: options.actorIds,
        content_locale: options.contentLocale,
        control: {
          mode: options.mode,
          max_steps: 100,
          max_scenes: 12,
          max_total_tokens: 500_000,
          max_runtime_seconds: 3_600,
          max_consecutive_model_failures: 3,
          pause_after_scene: options.mode !== 'autonomous',
          allow_user_override: true,
          checkpoint_every_steps: 5,
        },
        output: {
          manuscript_mode:
            options.mode === 'autonomous' ? 'after_scene' : 'manual',
          wiki_mode: 'after_scene',
        },
      } satisfies SimulationStartRequest
      const created = await perform('start', () =>
        engineRequest<SessionSnapshot>(`/projects/${projectId}/simulations`, {
          method: 'POST',
          body: JSON.stringify(request),
        })
      )
      if (created) {
        setSession(created)
        setLastStep(null)
        syncUrl(created)
      }
      return created
    },
    [perform, projectId, syncUrl]
  )

  const step = useCallback(async () => {
    if (!projectId || !session) return null
    const result = await perform('step', () =>
      engineRequest<StepResult>(
        `/projects/${projectId}/simulations/${session.session_id}/step`,
        { method: 'POST' }
      )
    )
    if (result) {
      setLastStep(result)
      await refreshSession()
    }
    return result
  }, [perform, projectId, refreshSession, session])

  const control = useCallback(
    async (action: 'run' | 'pause' | 'resume' | 'terminate' | 'cancel') => {
      if (!projectId || !session) return null
      const updated = await perform(action, () =>
        engineRequest<SessionSnapshot>(
          `/projects/${projectId}/simulations/${session.session_id}/${action}`,
          {
            method: 'POST',
            body:
              action === 'terminate' || action === 'cancel'
                ? JSON.stringify({ reason_text: `user requested ${action}` })
                : undefined,
          }
        )
      )
      if (updated) {
        setSession(updated)
        syncUrl(updated)
      }
      return updated
    },
    [perform, projectId, session, syncUrl]
  )

  const checkpoint = useCallback(async () => {
    if (!projectId || !session) return null
    const committed = await perform('checkpoint', () =>
      engineRequest<CommitResult>(
        `/projects/${projectId}/simulations/${session.session_id}/checkpoint`,
        { method: 'POST', body: JSON.stringify({ reason: 'user checkpoint' }) }
      )
    )
    if (committed) await refreshSession()
    return committed
  }, [perform, projectId, refreshSession, session])

  const retryMaintenance = useCallback(async () => {
    if (!projectId || !session) return null
    const updated = await perform('maintenance', () =>
      engineRequest<SessionSnapshot>(
        `/projects/${projectId}/simulations/${session.session_id}/maintenance/retry`,
        { method: 'POST' }
      )
    )
    if (updated) {
      setSession(updated)
      syncUrl(updated)
    }
    return updated
  }, [perform, projectId, session, syncUrl])

  const restore = useCallback(
    async (checkpointId: string) => {
      if (!projectId) return null
      const restored = await perform('restore', () =>
        engineRequest<SessionSnapshot>(
          `/projects/${projectId}/simulations/restore`,
          {
            method: 'POST',
            body: JSON.stringify({ checkpoint_id: checkpointId }),
          }
        )
      )
      if (restored) {
        setSession(restored)
        syncUrl(restored)
      }
      return restored
    },
    [perform, projectId, syncUrl]
  )

  const fork = useCallback(
    async (branchId: string) => {
      if (!projectId || !session?.checkpoint_id) return null
      const created = await perform('fork', () =>
        engineRequest<components['schemas']['BranchManifest']>(
          `/projects/${projectId}/branches`,
          {
            method: 'POST',
            body: JSON.stringify({
              branch_id: branchId,
              source_checkpoint_id: session.checkpoint_id,
              parent_branch_id: session.branch_id,
              content_locale: session.content_locale,
            }),
          }
        )
      )
      if (created) {
        setBranches((current) => [...current, created])
      }
      return created
    },
    [perform, projectId, session]
  )

  const compareBranches = useCallback(
    async (left: string, right: string) => {
      if (!projectId) return null
      const query = new URLSearchParams({ left, right })
      return perform('compare', () =>
        engineRequest<BranchComparison>(
          `/projects/${projectId}/branches/compare?${query.toString()}`
        )
      )
    },
    [perform, projectId]
  )

  const switchLocale = useCallback(
    async (contentLocale: string) => {
      if (!projectId || !session) return null
      const updated = await perform('locale', () =>
        engineRequest<SessionSnapshot>(
          `/projects/${projectId}/simulations/${session.session_id}/locale`,
          {
            method: 'POST',
            body: JSON.stringify({ content_locale: contentLocale }),
          }
        )
      )
      if (updated) setSession(updated)
      return updated
    },
    [perform, projectId, session]
  )

  const isPending = useCallback((operation: Operation) => pending.has(operation), [pending])
  const startNew = useCallback(() => {
    const currentBranch = session?.branch_id || 'main'
    setSession(null)
    setLastStep(null)
    setError(null)
    syncUrl(null, currentBranch)
  }, [session?.branch_id, syncUrl])

  const selectSession = useCallback(
    async (selectedSessionId: string) => {
      if (!projectId) return null
      const selected = await perform('load', () =>
        engineRequest<SessionSnapshot | SessionManifest>(
          `/projects/${projectId}/simulations/${selectedSessionId}`
        )
      )
      if (selected && 'request' in selected) {
        setSession(selected)
        setLastStep(null)
        syncUrl(selected)
        return selected
      }
      return null
    },
    [perform, projectId, syncUrl]
  )

  return {
    project,
    branches,
    sessions,
    session,
    lastStep,
    error,
    isPending,
    load,
    refreshSession,
    start,
    step,
    control,
    checkpoint,
    retryMaintenance,
    restore,
    fork,
    switchLocale,
    compareBranches,
    selectSession,
    startNew,
  }
}
