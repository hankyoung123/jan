import type { components } from '@story-engine/contracts'
import { useCallback, useEffect, useState } from 'react'

import { engineRequest } from '../engine'

export type WikiBranchView = components['schemas']['WikiBranchView']
export type WikiPage = components['schemas']['WikiPage']
export type WikiPageSummary = components['schemas']['WikiPageSummary']
export type DirectorInstruction = components['schemas']['DirectorInstruction']

export function useWiki(projectId: string | undefined, branchId: string) {
  const [view, setView] = useState<WikiBranchView | null>(null)
  const [instructions, setInstructions] = useState<DirectorInstruction[]>([])
  const [page, setPage] = useState<WikiPage | null>(null)
  const [loading, setLoading] = useState(Boolean(projectId))
  const [working, setWorking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadPage = useCallback(
    async (path: string) => {
      if (!projectId) return null
      const result = await engineRequest<WikiPage>(
        `/projects/${projectId}/branches/${branchId}/wiki/page?path=${encodeURIComponent(path)}`
      )
      setPage(result)
      return result
    },
    [branchId, projectId]
  )

  const load = useCallback(async () => {
    if (!projectId) {
      setView(null)
      setInstructions([])
      setPage(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [wiki, notes] = await Promise.all([
        engineRequest<WikiBranchView>(
          `/projects/${projectId}/branches/${branchId}/wiki`
        ),
        engineRequest<DirectorInstruction[]>(
          `/projects/${projectId}/branches/${branchId}/director-instructions`
        ),
      ])
      setView(wiki)
      setInstructions(notes)
      const preferred =
        wiki.pages.find((item) => item.path === page?.path)?.path ??
        wiki.pages.find((item) => item.path === 'world/state.md')?.path ??
        wiki.pages[0]?.path
      if (preferred) await loadPage(preferred)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Wiki 读取失败')
    } finally {
      setLoading(false)
    }
  }, [branchId, loadPage, page?.path, projectId])

  useEffect(() => {
    void load()
  }, [load])

  const rebuild = useCallback(async () => {
    if (!projectId || !view) return
    setWorking(true)
    setError(null)
    try {
      const rebuilt = await engineRequest<WikiBranchView>(
        `/projects/${projectId}/branches/${branchId}/wiki/rebuild`,
        {
          method: 'POST',
        }
      )
      setView(rebuilt)
      if (page) await loadPage(page.path)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Wiki 重建失败')
    } finally {
      setWorking(false)
    }
  }, [branchId, loadPage, page, projectId, view])

  const addInstruction = useCallback(
    async (text: string) => {
      if (!projectId || !view?.checkpoint_id) return null
      setWorking(true)
      setError(null)
      try {
        const instruction = await engineRequest<DirectorInstruction>(
          `/projects/${projectId}/branches/${branchId}/director-instructions`,
          {
            method: 'POST',
            body: JSON.stringify({
              checkpoint_id: view.checkpoint_id,
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
    [branchId, projectId, view?.checkpoint_id]
  )

  return {
    view,
    page,
    instructions,
    loading,
    working,
    error,
    load,
    loadPage,
    rebuild,
    addInstruction,
  }
}
