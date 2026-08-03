import type { components } from '@story-engine/contracts'
import { LockKeyhole, TriangleAlert } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { Button } from '@/components/ui/button'
import { StatusPill } from '../components/StoryLayout'
import { useWiki } from '../world/useWiki'

type StoryCharacter = components['schemas']['Character']

interface CharacterWikiViewProps {
  branchId: string
  characters: StoryCharacter[]
  projectId: string
}

export function CharacterWikiView({
  branchId,
  characters,
  projectId,
}: CharacterWikiViewProps) {
  const wiki = useWiki(projectId, branchId)
  const loadWikiPage = wiki.loadPage
  const [selectedId, setSelectedId] = useState(characters[0]?.id ?? '')
  const pages = useMemo(
    () =>
      wiki.view?.pages.filter((page) => page.subject_id === selectedId) ?? [],
    [selectedId, wiki.view?.pages]
  )

  useEffect(() => {
    if (!characters.some((character) => character.id === selectedId)) {
      setSelectedId(characters[0]?.id ?? '')
    }
  }, [characters, selectedId])

  useEffect(() => {
    const preferred =
      pages.find((page) => page.path.endsWith('/self.md'))?.path ??
      pages[0]?.path
    if (preferred && wiki.page?.path !== preferred) void loadWikiPage(preferred)
  }, [loadWikiPage, pages, wiki.page?.path])

  if (wiki.loading) {
    return <p className="text-sm text-muted-foreground">正在读取角色 Wiki…</p>
  }
  if (wiki.error) {
    return (
      <p
        className="border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive"
        role="alert"
      >
        {wiki.error}
      </p>
    )
  }

  return (
    <div className="grid min-h-[580px] border bg-background md:grid-cols-[220px_220px_1fr] xl:grid-cols-[220px_220px_1fr_260px]">
      <nav className="border-b bg-muted/20 p-2 md:border-b-0 md:border-r">
        <p className="px-3 pb-2 pt-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          Character Wiki
        </p>
        {characters.map((character) => (
          <Button
            className="mb-1 h-auto w-full justify-start px-3 py-3 text-left"
            key={character.id}
            onClick={() => setSelectedId(character.id)}
            variant={character.id === selectedId ? 'secondary' : 'ghost'}
          >
            <span className="min-w-0">
              <strong className="block truncate text-sm">
                {character.display_name || character.id}
              </strong>
              <span className="mt-1 block truncate text-xs font-normal text-muted-foreground">
                {character.identity}
              </span>
            </span>
          </Button>
        ))}
      </nav>

      <nav className="border-b p-2 md:border-b-0 md:border-r">
        <p className="px-3 pb-2 pt-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          私有页面
        </p>
        {pages.map((page) => (
          <button
            className={`mb-1 w-full px-3 py-2 text-left text-sm ${wiki.page?.path === page.path ? 'bg-foreground text-background' : 'text-muted-foreground hover:bg-accent hover:text-foreground'}`}
            key={page.path}
            onClick={() => void wiki.loadPage(page.path)}
            type="button"
          >
            <span className="block truncate">{page.title}</span>
            <span className="mt-1 block font-mono text-[10px] opacity-70">
              S{page.updated_at_step}
            </span>
          </button>
        ))}
        {!pages.length && (
          <p className="px-3 py-4 text-xs leading-5 text-muted-foreground">
            该角色尚无场景边界知识。
          </p>
        )}
      </nav>

      <main className="min-w-0 p-6 lg:p-8">
        {wiki.view?.stale && (
          <p className="mb-5 flex items-center gap-2 border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-800">
            <TriangleAlert size={15} /> 此分支 Wiki 需要重新整理。
          </p>
        )}
        {wiki.page && wiki.page.subject_id === selectedId ? (
          <article className="prose prose-neutral max-w-3xl dark:prose-invert">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {wiki.page.content}
            </ReactMarkdown>
          </article>
        ) : (
          <p className="text-sm text-muted-foreground">选择一个角色 Wiki 页面。</p>
        )}
      </main>

      <aside className="border-t bg-muted/15 p-5 xl:border-l xl:border-t-0">
        <p className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
          <LockKeyhole size={13} /> Private lineage
        </p>
        {wiki.page && wiki.page.subject_id === selectedId ? (
          <dl className="mt-4 space-y-4 text-xs">
            <div>
              <dt className="text-muted-foreground">页面</dt>
              <dd className="mt-1 break-all font-mono">{wiki.page.path}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">状态</dt>
              <dd className="mt-1 flex gap-2">
                <StatusPill tone={wiki.page.stale ? 'warning' : 'success'}>
                  {wiki.page.stale ? 'stale' : 'current'}
                </StatusPill>
                <span>置信度 {wiki.page.confidence.toFixed(2)}</span>
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Source IDs</dt>
              <dd className="mt-2 space-y-1 break-all font-mono text-[10px]">
                {wiki.page.source_ids.map((sourceId) => (
                  <div key={sourceId}>{sourceId}</div>
                ))}
              </dd>
            </div>
          </dl>
        ) : (
          <p className="mt-4 text-sm text-muted-foreground">
            角色仅能读取自己的私有 Wiki 与公共 World Wiki。
          </p>
        )}
      </aside>
    </div>
  )
}
