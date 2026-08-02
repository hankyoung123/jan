import { Link } from '@tanstack/react-router'
import { BookOpenText, RefreshCw, Send } from 'lucide-react'
import { useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { route } from '@/constants/routes'
import { useActiveStoryProjectId } from '../activeProject'
import { PageHeader, StoryPage } from '../components/StoryLayout'
import { useBranchContext } from '../useBranchContext'
import { WorldBibleNavigation, type WorldSection } from './WorldBibleNavigation'
import { WorldEntryInspector } from './WorldEntryInspector'
import { WorldEntryList } from './WorldEntryList'
import { useWorldBible, type WorldBibleEntry } from './useWorldBible'

export function WorldView() {
  const projectId = useActiveStoryProjectId() ?? undefined
  const { branchId, setBranchId } = useBranchContext()
  const world = useWorldBible(projectId, branchId)
  const [section, setSection] = useState<WorldSection>('overview')
  const [selected, setSelected] = useState<WorldBibleEntry | null>(null)
  const [note, setNote] = useState('')

  const entries = useMemo(() => {
    if (!world.snapshot || section === 'overview' || section === 'director_notes') {
      return []
    }
    return world.snapshot[section] as WorldBibleEntry[]
  }, [section, world.snapshot])

  if (!projectId) {
    return (
      <StoryPage>
        <PageHeader eyebrow="World Bible" title="世界设定" />
        <section className="border bg-background p-8 text-center">
          <BookOpenText className="mx-auto text-muted-foreground" />
          <p className="mt-4 text-sm text-muted-foreground">先选择一个故事项目。</p>
          <Button asChild className="mt-5" variant="outline"><Link to={route.submission}>前往投稿</Link></Button>
        </section>
      </StoryPage>
    )
  }

  return (
    <StoryPage>
      <PageHeader
        eyebrow={world.snapshot ? `${branchId} · ${world.snapshot.checkpoint_id}` : branchId}
        title="世界设定"
        action={
          <div className="flex items-center gap-2">
            <Input
              aria-label="世界设定分支"
              className="h-9 w-32 font-mono text-xs"
              onBlur={(event) => setBranchId(event.target.value)}
              defaultValue={branchId}
            />
            <Button disabled={world.working || !world.snapshot} onClick={() => void world.rebuild()} size="sm" variant="outline">
              <RefreshCw size={14} /> 重建
            </Button>
          </div>
        }
      />
      {world.loading ? (
        <p className="text-sm text-muted-foreground">正在重建世界视图…</p>
      ) : world.error ? (
        <p className="border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive" role="alert">{world.error}</p>
      ) : world.snapshot ? (
        <div className="grid min-h-[580px] border bg-background md:grid-cols-[190px_1fr] lg:grid-cols-[190px_1fr_250px]">
          <WorldBibleNavigation active={section} onChange={(value) => { setSection(value); setSelected(null) }} />
          <main className="p-6 lg:p-8">
            {section === 'overview' ? (
              <div className="max-w-3xl">
                <p className="font-mono text-[11px] text-muted-foreground">{world.snapshot.project_id} / {world.snapshot.branch_id}</p>
                <h2 className="mt-3 font-studio text-2xl">创作方向</h2>
                <p className="mt-3 whitespace-pre-line text-sm leading-7 text-muted-foreground">{world.snapshot.creative_direction_text}</p>
                <div className="my-7 h-px bg-border" />
                <h2 className="font-studio text-2xl">当前世界状态</h2>
                <p className="mt-3 whitespace-pre-line text-sm leading-7">{world.snapshot.current_world_state_text}</p>
              </div>
            ) : section === 'director_notes' ? (
              <div className="max-w-3xl">
                <h2 className="font-studio text-2xl">导演补充</h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">补充不会改写已经发生的历史；它会从当前 Checkpoint 起注入下一次 GM 上下文。</p>
                <textarea className="mt-5 min-h-32 w-full border bg-background p-3 text-sm outline-none focus:border-amber-500" onChange={(event) => setNote(event.target.value)} value={note} />
                <Button className="mt-3" disabled={!note.trim() || world.working} onClick={async () => { if (await world.addInstruction(note.trim())) setNote('') }}><Send size={14} /> 保存导演补充</Button>
                <div className="mt-8 divide-y border-y">
                  {world.instructions.map((item) => (
                    <article className="py-4" key={item.instruction_id}>
                      <p className="text-sm leading-6">{item.text}</p>
                      <p className="mt-2 break-all font-mono text-[10px] text-muted-foreground">{item.applies_from_checkpoint_id}</p>
                    </article>
                  ))}
                </div>
              </div>
            ) : (
              <WorldEntryList entries={entries} onSelect={setSelected} selectedId={selected?.entry_id} />
            )}
          </main>
          <WorldEntryInspector branchId={branchId} entry={selected} />
        </div>
      ) : null}
    </StoryPage>
  )
}
