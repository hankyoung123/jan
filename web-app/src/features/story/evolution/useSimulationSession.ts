import type { components } from '@story-engine/contracts'
import { useCallback, useEffect, useState } from 'react'

import { engineRequest } from '../engine'

export type ProjectSnapshot = components['schemas']['ProjectSnapshot']
export type SessionSnapshot = components['schemas']['TurnSessionSnapshot']
export type StepResult = components['schemas']['StepResult']
export type CommitResult = components['schemas']['CommitResult']
export type ControlMode = components['schemas']['ControlMode']
export type BranchManifest = components['schemas']['BranchManifest']
export type BranchComparison = components['schemas']['BranchComparisonResponse']

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
  | 'projection'
  | 'locale'
  | 'compare'

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
  const refreshSession = useCallback(async () => {
    if (!projectId || !sessionId) return
    const updated = await engineRequest<SessionSnapshot>(
      `/projects/${projectId}/simulations/${sessionId}`
    )
    setSession(updated)
  }, [projectId, sessionId])

  const load = useCallback(async () => {
    if (!projectId) {
      setProject(null)
      setSession(null)
      return
    }
    const loaded = await perform('load', async () => {
      const [projectSnapshot, sessions, loadedBranches] = await Promise.all([
        engineRequest<ProjectSnapshot>(`/projects/${projectId}`),
        engineRequest<SessionSnapshot[]>(`/projects/${projectId}/simulations`),
        engineRequest<BranchManifest[]>(`/projects/${projectId}/branches`).catch(
          () => []
        ),
      ])
      return { projectSnapshot, sessions, loadedBranches }
    })
    if (!loaded) return
    setProject(loaded.projectSnapshot)
    setBranches(loaded.loadedBranches)
    const resumable = loaded.sessions
      .filter((item) => !['terminated', 'cancelled', 'failed'].includes(item.status))
      .sort((left, right) => right.updated_at.localeCompare(left.updated_at))[0]
    if (resumable) {
      setSession(resumable)
      return
    }
    if (loaded.sessions.length === 0) {
      try {
        const branch = await engineRequest<components['schemas']['BranchManifest']>(
          `/projects/${projectId}/branches/main`
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
          return
        }
      } catch {
        // A new project legitimately has neither a branch nor a checkpoint.
      }
    }
    setSession(null)
  }, [perform, projectId])

  useEffect(() => {
    void load()
  }, [load])

  const start = useCallback(
    async (options: StartOptions) => {
      if (!projectId) return null
      const created = await perform('start', () =>
        engineRequest<SessionSnapshot>(`/projects/${projectId}/simulations`, {
          method: 'POST',
          body: JSON.stringify({
            branch_id: options.branchId,
            premise_text: options.premise,
            actor_ids: options.actorIds,
            content_locale: options.contentLocale,
            control: {
              mode: options.mode,
              max_steps: 100,
              max_scenes: 12,
              pause_after_scene: options.mode !== 'autonomous',
              checkpoint_every_steps: 5,
            },
          }),
        })
      )
      if (created) {
        setSession(created)
        setLastStep(null)
      }
      return created
    },
    [perform, projectId]
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
      if (updated) setSession(updated)
      return updated
    },
    [perform, projectId, session]
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
      if (restored) setSession(restored)
      return restored
    },
    [perform, projectId]
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

  const rebuildProjection = useCallback(async () => {
    if (!projectId || !session) return null
    return perform('projection', () =>
      engineRequest<components['schemas']['ProjectionResponse']>(
        `/projects/${projectId}/branches/${session.branch_id}/projection`,
        {
          method: 'POST',
          body: JSON.stringify({ checkpoint_id: session.checkpoint_id }),
        }
      )
    )
  }, [perform, projectId, session])

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
    setSession(null)
    setLastStep(null)
    setError(null)
  }, [])

  return {
    project,
    branches,
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
    restore,
    fork,
    rebuildProjection,
    switchLocale,
    compareBranches,
    startNew,
  }
}
