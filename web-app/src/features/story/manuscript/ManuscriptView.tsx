import { Link } from '@tanstack/react-router'
import { Download, FileText, Sparkles } from 'lucide-react'
import { lazy, Suspense, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { route } from '@/constants/routes'
import type { NovelManuscriptValue } from '@/editor/NovelManuscriptEditor'
import { novelDocumentFromMarkdown } from '@/editor/manuscriptMarkdown'
import { useActiveStoryProjectId } from '../activeProject'
import { PageHeader, StoryPage } from '../components/StoryLayout'
import { engineRequest } from '../engine'
import { useBranchContext } from '../useBranchContext'
import { useProjectModelMessages } from '../useProjectModelMessages'
import { NarrativeSourcePicker } from './NarrativeSourcePicker'
import { SourceInspector } from './SourceInspector'
import { useManuscript, type NarrativeSourceSummary, type SceneDraft } from './useManuscript'

const NovelManuscriptEditor = lazy(() =>
  import('@/editor/NovelManuscriptEditor').then((module) => ({ default: module.NovelManuscriptEditor }))
)

export function ManuscriptView() {
  const projectId = useActiveStoryProjectId() ?? undefined
  const { branchId, setBranchId } = useBranchContext()
  const manuscript = useManuscript(projectId, branchId)
  const projectMessages = useProjectModelMessages(projectId)
  const [source, setSource] = useState<NarrativeSourceSummary | null>(null)
  const [sceneId, setSceneId] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [dirty, setDirty] = useState(false)
  const [viewpoint, setViewpoint] = useState('')
  const [notice, setNotice] = useState<string | null>(null)

  const scene = useMemo(
    () => manuscript.scenes.find((item) => item.id === sceneId) ?? null,
    [manuscript.scenes, sceneId]
  )
  const chapters = useMemo(
    () => [...new Set(manuscript.scenes.map((item) => item.chapter_id))],
    [manuscript.scenes]
  )
  const activityMessages = useMemo(
    () =>
      projectMessages.messages.filter(
        (message) =>
          message.metadata?.branchId === branchId &&
          (message.metadata?.stage === 'writer' ||
            message.metadata?.stage === 'editor') &&
          (!scene || message.metadata?.step === scene.source_to_step)
      ),
    [branchId, projectMessages.messages, scene]
  )

  useEffect(() => {
    setSource(null)
    setSceneId(null)
    setTitle('')
    setBody('')
    setDirty(false)
    setViewpoint('')
    setNotice(null)
  }, [branchId, projectId])

  useEffect(() => {
    if (!source) setSource(manuscript.sources.find((item) => item.status === 'available') ?? manuscript.sources.at(-1) ?? null)
  }, [manuscript.sources, source])

  useEffect(() => {
    if (!sceneId && manuscript.scenes.length) {
      const latest = manuscript.scenes.at(-1)!
      openScene(latest)
    }
  }, [manuscript.scenes, sceneId])

  function openScene(next: SceneDraft) {
    setSceneId(next.id)
    setTitle(next.title)
    setBody(next.body)
    setDirty(false)
    setNotice(null)
  }

  async function generate() {
    if (!source) return
    const generated = await manuscript.generate(
      source,
      chapters.at(-1) ?? 'chapter-001',
      viewpoint || null
    )
    if (generated) {
      openScene(generated)
      setNotice(`${generated.id} 已从模拟历史生成`)
    }
  }

  async function save() {
    if (!scene || !title.trim() || !body.trim()) return
    const result = await manuscript.save(scene, title.trim(), body)
    if (!result) return
    openScene(result.draft)
    setNotice(result.status === 'saved' ? '正文与来源元数据已保存' : '发现来源不支持的事实，请改写或作为导演指令注入下一次模拟')
  }

  async function sendUnsupportedFactsToDirector() {
    if (!projectId || !scene?.review?.unsupported_facts.length) return
    await engineRequest(
      `/projects/${projectId}/branches/${branchId}/director-instructions`,
      {
        method: 'POST',
        body: JSON.stringify({
          checkpoint_id: scene.source_checkpoint_id,
          text: scene.review.unsupported_facts.join('\n'),
        }),
      }
    )
    setNotice('未支持事实已保存为导演指令；现有模拟历史未被改写')
  }

  async function exportMarkdown() {
    const exported = await manuscript.exportMarkdown()
    if (!exported) return
    const url = URL.createObjectURL(new Blob([exported.markdown], { type: 'text/markdown' }))
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = exported.filename
    anchor.click()
    URL.revokeObjectURL(url)
    setNotice(`已导出 ${exported.filename}`)
  }

  if (!projectId) {
    return <StoryPage><PageHeader eyebrow="Narrative Source" title="章节正文" /><section className="border bg-background p-8 text-center"><FileText className="mx-auto text-muted-foreground" /><p className="mt-4 text-sm text-muted-foreground">先选择一个故事项目。</p><Button asChild className="mt-5" variant="outline"><Link to={route.submission}>前往投稿</Link></Button></section></StoryPage>
  }

  return (
    <StoryPage>
      <PageHeader
        eyebrow={`${branchId} · ${manuscript.sources.length} 个正文来源`}
        title="章节正文"
        action={<div className="flex items-center gap-2"><Input aria-label="正文分支" className="h-9 w-32 font-mono text-xs" defaultValue={branchId} onBlur={(event) => setBranchId(event.target.value)} /><Button disabled={manuscript.working !== null} onClick={() => void exportMarkdown()} size="sm" variant="outline"><Download size={14} /> 导出</Button></div>}
      />
      {manuscript.error && <p className="mb-4 border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">{manuscript.error}</p>}
      <div className="grid min-h-[640px] border bg-background lg:grid-cols-[260px_1fr_270px]">
        <nav className="border-b p-4 lg:border-b-0 lg:border-r">
          <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">Simulation Sources</p>
          <div className="mt-3"><NarrativeSourcePicker onSelect={(next) => { setSource(next); setViewpoint(next.available_viewpoint_ids[0] ?? '') }} selected={source} sources={manuscript.sources} /></div>
          {source && (
            <div className="mt-5 border-t pt-4">
              <label className="text-xs text-muted-foreground" htmlFor="writer-viewpoint">叙事视角</label>
              <select className="mt-2 h-9 w-full border bg-background px-2 text-sm" id="writer-viewpoint" onChange={(event) => setViewpoint(event.target.value)} value={viewpoint}>
                <option value="">自动选择主视角</option>
                {source.available_viewpoint_ids.map((id) => <option key={id} value={id}>{id}</option>)}
              </select>
              <Button className="mt-3 w-full" disabled={manuscript.working !== null} onClick={() => void generate()}><Sparkles size={14} /> 从此片段生成</Button>
            </div>
          )}
          <div className="mt-6 border-t pt-4">
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">Scenes</p>
            {manuscript.scenes.map((item) => <button className={`mt-2 w-full px-2 py-2 text-left text-sm ${item.id === sceneId ? 'bg-foreground text-background' : 'hover:bg-muted'}`} key={item.id} onClick={() => openScene(item)} type="button"><span className="block truncate">{item.title}</span><span className="mt-1 block font-mono text-[10px] opacity-60">{item.id} · {item.status}</span></button>)}
          </div>
        </nav>
        <main className="min-w-0 p-5 lg:p-7">
          {scene ? (
            <><Input aria-label="场景标题" className="border-0 px-0 font-studio text-2xl shadow-none" onChange={(event) => { setTitle(event.target.value); setDirty(true) }} value={title} /><div className="my-4 h-px bg-border" /><Suspense fallback={<p className="text-sm text-muted-foreground">正在加载正文编辑器…</p>}><NovelManuscriptEditor initialContent={novelDocumentFromMarkdown(body)} key={`${branchId}:${scene.id}:${scene.revision}`} onChange={(value: NovelManuscriptValue) => { setBody(value.markdown); setDirty(true) }} /></Suspense><div className="mt-5 flex flex-wrap items-center gap-3"><Button disabled={manuscript.working !== null || (!dirty && scene.status === 'saved')} onClick={() => void save()}>保存并检查来源</Button>{!dirty && scene.review?.unsupported_facts.length ? <Button onClick={() => void sendUnsupportedFactsToDirector()} variant="outline">转为导演指令</Button> : null}<span className="text-xs text-muted-foreground">revision {scene.revision} · scene v{scene.base_scene_version}</span></div></>
          ) : <div className="grid min-h-[520px] place-items-center text-center"><div><FileText className="mx-auto text-muted-foreground" /><h2 className="mt-4 font-studio text-xl">选择模拟片段生成第一幕</h2><p className="mt-2 max-w-sm text-sm leading-6 text-muted-foreground">Writer 直接读取 Branch、Checkpoint、ResolvedEvent 与合法视角记忆。</p></div></div>}
        </main>
        <SourceInspector
          activityMessages={activityMessages}
          scene={scene}
          showReview={!dirty}
        />
      </div>
      {notice && <p className="mt-4 bg-primary/10 p-3 text-sm text-primary" role="status">{notice}</p>}
    </StoryPage>
  )
}
