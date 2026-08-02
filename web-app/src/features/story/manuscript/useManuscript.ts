import type { components } from '@story-engine/contracts'
import { useCallback, useEffect, useState } from 'react'

import { engineRequest } from '../engine'

export type NarrativeSourceSummary = components['schemas']['NarrativeSourceSummary']
export type SceneDraft = components['schemas']['SceneDraft']
export type SceneMutationResult = components['schemas']['SceneMutationResult']
export type ManuscriptExport = components['schemas']['ManuscriptExport']

export function useManuscript(projectId: string | undefined, branchId: string) {
  const [sources, setSources] = useState<NarrativeSourceSummary[]>([])
  const [scenes, setScenes] = useState<SceneDraft[]>([])
  const [loading, setLoading] = useState(Boolean(projectId))
  const [working, setWorking] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    if (!projectId) {
      setSources([])
      setScenes([])
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [loadedSources, loadedScenes] = await Promise.all([
        engineRequest<NarrativeSourceSummary[]>(
          `/projects/${projectId}/branches/${branchId}/narrative-sources`
        ),
        engineRequest<SceneDraft[]>(
          `/projects/${projectId}/branches/${branchId}/manuscript/scenes`
        ),
      ])
      setSources(loadedSources)
      setScenes([...loadedScenes].sort((a, b) => a.sequence - b.sequence))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '正文工作区读取失败')
    } finally {
      setLoading(false)
    }
  }, [branchId, projectId])

  useEffect(() => {
    void load()
  }, [load])

  const replaceScene = useCallback((scene: SceneDraft) => {
    setScenes((current) =>
      [...current.filter((item) => item.id !== scene.id), scene].sort(
        (a, b) => a.sequence - b.sequence
      )
    )
  }, [])

  const generate = useCallback(
    async (
      source: NarrativeSourceSummary,
      chapterId: string,
      viewpointActorId: string | null
    ) => {
      if (!projectId) return null
      setWorking('generate')
      setError(null)
      try {
        const scene = await engineRequest<SceneDraft>(
          `/projects/${projectId}/branches/${branchId}/manuscript/scenes/generate`,
          {
            method: 'POST',
            body: JSON.stringify({
              checkpoint_id: source.checkpoint_id,
              from_step: source.from_step,
              to_step: source.to_step,
              chapter_id: chapterId,
              viewpoint_actor_id: viewpointActorId,
            }),
          }
        )
        replaceScene(scene)
        await load()
        return scene
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '正文生成失败')
        return null
      } finally {
        setWorking(null)
      }
    },
    [branchId, load, projectId, replaceScene]
  )

  const save = useCallback(
    async (scene: SceneDraft, title: string, body: string) => {
      if (!projectId) return null
      setWorking('save')
      setError(null)
      try {
        const result = await engineRequest<SceneMutationResult>(
          `/projects/${projectId}/branches/${branchId}/manuscript/scenes/${scene.id}`,
          {
            method: 'PUT',
            body: JSON.stringify({
              title,
              body,
              expected_revision: scene.revision,
              expected_scene_version: scene.base_scene_version,
            }),
          }
        )
        replaceScene(result.draft)
        return result
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : '正文保存失败')
        return null
      } finally {
        setWorking(null)
      }
    },
    [branchId, projectId, replaceScene]
  )

  const exportMarkdown = useCallback(async () => {
    if (!projectId) return null
    setWorking('export')
    setError(null)
    try {
      return await engineRequest<ManuscriptExport>(
        `/projects/${projectId}/branches/${branchId}/manuscript/export`
      )
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '正文导出失败')
      return null
    } finally {
      setWorking(null)
    }
  }, [branchId, projectId])

  return {
    sources,
    scenes,
    loading,
    working,
    error,
    load,
    generate,
    save,
    exportMarkdown,
  }
}
