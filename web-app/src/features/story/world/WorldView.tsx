import { Link } from '@tanstack/react-router'
import { BookOpenText, RefreshCw, Send, TriangleAlert } from 'lucide-react'
import { useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { route } from '@/constants/routes'
import { useActiveStoryProjectId } from '../activeProject'
import { AgentMessageList } from '../components/AgentMessageList'
import { PageHeader, StatusPill, StoryPage } from '../components/StoryLayout'
import { useBranchContext } from '../useBranchContext'
import { useProjectModelMessages } from '../useProjectModelMessages'
import { useWiki } from './useWiki'

export function WorldView() {
  const projectId = useActiveStoryProjectId() ?? undefined
  const { branchId, setBranchId } = useBranchContext()
  const wiki = useWiki(projectId, branchId)
  const projectMessages = useProjectModelMessages(projectId)
  const [showInstructions, setShowInstructions] = useState(false)
  const [note, setNote] = useState('')
  const worldPages = useMemo(
    () => wiki.view?.pages.filter((item) => item.path.startsWith('world/')) ?? [],
    [wiki.view?.pages]
  )
  const wikiMessages = useMemo(
    () =>
      projectMessages.messages.filter(
        (message) =>
          message.metadata?.branchId === branchId &&
          message.metadata?.stage === 'wiki'
      ),
    [branchId, projectMessages.messages]
  )

  if (!projectId) {
    return (
      <StoryPage>
        <PageHeader eyebrow="LLM Wiki" title="世界知识" />
        <section className="border bg-background p-8 text-center">
          <BookOpenText className="mx-auto text-muted-foreground" />
          <p className="mt-4 text-sm text-muted-foreground">先选择一个故事项目。</p>
          <Button asChild className="mt-5" variant="outline">
            <Link to={route.submission}>前往投稿</Link>
          </Button>
        </section>
      </StoryPage>
    )
  }

  return (
    <StoryPage wide>
      <PageHeader
        eyebrow={wiki.view ? `${branchId} · Step ${wiki.view.updated_at_step}` : branchId}
        title="世界 Wiki"
        action={
          <div className="flex items-center gap-2">
            <Input
              aria-label="Wiki 分支"
              className="h-9 w-32 font-mono text-xs"
              defaultValue={branchId}
              onBlur={(event) => setBranchId(event.target.value)}
            />
            <Button
              disabled={wiki.working}
              onClick={() => void wiki.rebuild()}
              size="sm"
              variant="outline"
            >
              <RefreshCw size={14} /> 重新整理
            </Button>
          </div>
        }
      />
      {wiki.loading ? (
        <p className="text-sm text-muted-foreground">正在读取 Wiki…</p>
      ) : wiki.error ? (
        <p className="border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive" role="alert">
          {wiki.error}
        </p>
      ) : wiki.view ? (
        <div className="grid min-h-[620px] border bg-background md:grid-cols-[240px_1fr] xl:grid-cols-[240px_1fr_280px]">
          <nav className="border-b bg-muted/20 p-2 md:border-b-0 md:border-r">
            <p className="px-3 pb-2 pt-3 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
              World Wiki
            </p>
            {worldPages.map((item) => (
              <button
                className={`mb-1 w-full px-3 py-2 text-left text-sm ${wiki.page?.path === item.path && !showInstructions ? 'bg-foreground text-background' : 'text-muted-foreground hover:bg-accent hover:text-foreground'}`}
                key={item.path}
                onClick={() => {
                  setShowInstructions(false)
                  void wiki.loadPage(item.path)
                }}
                type="button"
              >
                <span className="block truncate">{item.title}</span>
                <span className="mt-1 block font-mono text-[10px] opacity-70">S{item.updated_at_step}</span>
              </button>
            ))}
            <button
              className={`mt-3 w-full px-3 py-2 text-left text-sm ${showInstructions ? 'bg-foreground text-background' : 'text-muted-foreground hover:bg-accent hover:text-foreground'}`}
              onClick={() => setShowInstructions(true)}
              type="button"
            >
              导演补充
            </button>
          </nav>

          <main className="min-w-0 p-6 lg:p-8">
            {wiki.view.degraded ? (
              <p
                className="mb-5 flex items-center gap-2 border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-800"
                role="status"
              >
                <TriangleAlert size={15} />
                Wiki 维护已降级。模拟可继续，但正文生成已暂停：
                {wiki.view.degradation_reason}
              </p>
            ) : wiki.view.stale ? (
              <p className="mb-5 flex items-center gap-2 border border-amber-500/30 bg-amber-500/10 p-3 text-sm text-amber-800">
                <TriangleAlert size={15} /> 此 Wiki 已回滚，等待从目标 Checkpoint 重新整理。
              </p>
            ) : null}
            {showInstructions ? (
              <div className="max-w-3xl">
                <h2 className="font-studio text-2xl">导演补充</h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  补充只影响后续 GM 世界判断，并作为 Wiki 原始来源保留。
                </p>
                <textarea
                  className="mt-5 min-h-32 w-full border bg-background p-3 text-sm outline-none focus:border-amber-500"
                  onChange={(event) => setNote(event.target.value)}
                  value={note}
                />
                <Button
                  className="mt-3"
                  disabled={!note.trim() || wiki.working || !wiki.view.checkpoint_id}
                  onClick={async () => {
                    if (await wiki.addInstruction(note.trim())) setNote('')
                  }}
                >
                  <Send size={14} /> 保存导演补充
                </Button>
                <div className="mt-8 divide-y border-y">
                  {wiki.instructions.map((item) => (
                    <article className="py-4" key={item.instruction_id}>
                      <p className="text-sm leading-6">{item.text}</p>
                      <p className="mt-2 break-all font-mono text-[10px] text-muted-foreground">
                        {item.applies_from_checkpoint_id}
                      </p>
                    </article>
                  ))}
                </div>
              </div>
            ) : wiki.page ? (
              <article className="prose prose-neutral max-w-3xl dark:prose-invert">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{wiki.page.content}</ReactMarkdown>
              </article>
            ) : (
              <p className="text-sm text-muted-foreground">选择一个 Wiki 页面。</p>
            )}
          </main>

          <aside className="border-t bg-muted/15 p-5 xl:border-l xl:border-t-0">
            <p className="text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
              Source lineage
            </p>
            {wiki.page && !showInstructions ? (
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
                    {wiki.page.source_ids.map((id) => <div key={id}>{id}</div>)}
                  </dd>
                </div>
              </dl>
            ) : (
              <p className="mt-4 text-sm text-muted-foreground">选择页面查看来源。</p>
            )}
            {wikiMessages.length > 0 && (
              <section className="mt-6 border-t pt-5">
                <p className="mb-4 text-[10px] font-semibold uppercase tracking-[0.2em] text-muted-foreground">
                  Wiki Agent Activity
                </p>
                <AgentMessageList messages={wikiMessages} preset="live-agent" />
              </section>
            )}
          </aside>
        </div>
      ) : null}
    </StoryPage>
  )
}
