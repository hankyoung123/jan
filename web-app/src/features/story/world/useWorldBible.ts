import type { components } from '@story-engine/contracts'
import { useCallback, useEffect, useState } from 'react'

import { engineRequest } from '../engine'

export type WorldBibleSnapshot = components['schemas']['WorldBibleSnapshot']
export type WorldBibleEntry = components['schemas']['WorldBibleEntry']
export type DirectorInstruction = components['schemas']['DirectorInstruction']

export function useWorldBible(projectId: string | undefined, branchId: string) {
  const [snapshot, setSnapshot] = useState<WorldBibleSnapshot | null>(null)
  const [instructions, setInstructions] = useState<DirectorInstruction[]>([])
  const [loading, setLoading] = useState(Boolean(projectId))
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!projectId) {
      setSnapshot(null)
      setInstructions([])
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [world, notes] = await Promise.all([
        engineRequest<WorldBibleSnapshot>(
          `/projects/${projectId}/branches/${branchId}/world-bible`
        ),
        engineRequest<DirectorInstruction[]>(
          `/projects/${projectId}/branches/${branchId}/director-instructions`
        ),
      ])
      setSnapshot(world)
      setInstructions(notes)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '世界设定读取失败')
    } finally {
      setLoading(false)
    }
  }, [branchId, projectId])

  useEffect(() => {
    void load()
  }, [load])

  const rebuild = useCallback(async () => {
    if (!projectId || !snapshot) return
    setWorking(true)
    setError(null)
    try {
      const rebuilt = await engineRequest<WorldBibleSnapshot>(
        `/projects/${projectId}/branches/${branchId}/world-bible/rebuild`,
        {
          method: 'POST',
          body: JSON.stringify({ checkpoint_id: snapshot.checkpoint_id }),
        }
      )
      setSnapshot(rebuilt)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '世界设定重建失败')
    } finally {
      setWorking(false)
    }
  }, [branchId, projectId, snapshot])

  const addInstruction = useCallback(
    async (text: string) => {
      if (!projectId || !snapshot) return null
      setWorking(true)
      setError(null)
      try {
        const instruction = await engineRequest<DirectorInstruction>(
          `/projects/${projectId}/branches/${branchId}/director-instructions`,
          {
            method: 'POST',
            body: JSON.stringify({
              checkpoint_id: snapshot.checkpoint_id,
              text,
            }),
          }
        )
        setInstructions((current) => [...current, instruction])
        return instruction
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '导演补充保存失败')
        return null
      } finally {
        setWorking(false)
      }
    },
    [branchId, projectId, snapshot]
  )

  return {
    snapshot,
    instructions,
    loading,
    working,
    error,
    load,
    rebuild,
    addInstruction,
  }
}
