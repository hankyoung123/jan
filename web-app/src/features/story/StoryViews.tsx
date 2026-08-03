import type { components } from '@story-engine/contracts'
import { Link } from '@tanstack/react-router'
import type { ChatStatus, UIMessage } from 'ai'
import {
  ArrowRight,
  BookOpenText,
  Check,
  FileText,
  FolderOpen,
  LockKeyhole,
  Network,
  Sparkles,
  TriangleAlert,
  UsersRound,
} from 'lucide-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react'

import {
  JanChatComposer,
  JanChatShell,
} from '@/components/ai-elements/jan-chat-shell'
import { Button } from '@/components/ui/button'
import { route } from '@/constants/routes'
import {
  clearActiveStoryProject,
  setActiveStoryProjectId,
  useActiveStoryProjectId,
} from './activeProject'
import {
  PageHeader,
  primaryButton,
  StatusPill,
  StoryPage,
  StoryViewToggle,
} from './components/StoryLayout'
import { engineRequest, subscribeProjectEvents } from './engine'
import { useBranchContext } from './useBranchContext'
import {
  resetSubmissionSession,
  useSubmissionSession,
} from './submission/session'
import { CharacterWikiView } from './character/CharacterWikiView'

type SubmissionPackage = components['schemas']['SubmissionPackage']
type SubmissionConversationResponse =
  components['schemas']['SubmissionConversationResponse']
type SubmissionMessage = components['schemas']['Message']
type ProjectSnapshot = components['schemas']['ProjectSnapshot']
type StoryCharacter = components['schemas']['Character']
type ProjectCatalogEntry = components['schemas']['ProjectCatalogEntry']
type WorkspaceState = components['schemas']['WorkspaceState']
type PromotionAssessment = components['schemas']['PromotionAssessment']
type PromotionCommitResult = components['schemas']['PromotionCommitResult']

export function WorkbenchView() {
  const activeProjectId = useActiveStoryProjectId()
  const [projects, setProjects] = useState<ProjectCatalogEntry[]>([])
  const [workspace, setWorkspace] = useState<WorkspaceState | null>(null)
  const [loading, setLoading] = useState(true)
  const [workingProjectId, setWorkingProjectId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [watchError, setWatchError] = useState<string | null>(null)

  useEffect(() => {
    let disposed = false
    setLoading(true)
    void engineRequest<ProjectCatalogEntry[]>('/projects')
      .then((catalog) => {
        if (!disposed) setProjects(catalog)
      })
      .catch((cause: unknown) => {
        if (!disposed) {
          setError(cause instanceof Error ? cause.message : '项目列表读取失败')
        }
      })
      .finally(() => {
        if (!disposed) setLoading(false)
      })
    return () => {
      disposed = true
    }
  }, [])

  useEffect(() => {
    if (!activeProjectId) {
      setWorkspace(null)
      setWatchError(null)
      return
    }
    let disposed = false
    setWorkingProjectId(activeProjectId)
    setError(null)
    void engineRequest<WorkspaceState>(`/projects/${activeProjectId}/open`, {
      method: 'POST',
    })
      .then((opened) => {
        if (disposed) return
        setWorkspace(opened)
        setProjects((current) =>
          current.map((project) => ({
            ...project,
            is_open: project.id === activeProjectId,
            world_version:
              project.id === activeProjectId
                ? opened.index.world_version
                : project.world_version,
          }))
        )
      })
      .catch((cause: unknown) => {
        if (!disposed) {
          setError(cause instanceof Error ? cause.message : '项目打开失败')
          clearActiveStoryProject()
        }
      })
      .finally(() => {
        if (!disposed) setWorkingProjectId(null)
      })
    return () => {
      disposed = true
    }
  }, [activeProjectId])

  useEffect(() => {
    const projectId = workspace?.project.project.id
    if (!projectId) return
    let disposed = false
    let cleanup: () => void = () => undefined
    void subscribeProjectEvents(projectId, (event) => {
      const resyncRequired = event.type === 'stream.resync_required'
      if (event.type !== 'workspace.changed' && !resyncRequired) return
      if (!resyncRequired && event.payload.status === 'error') {
        const message = event.payload.error
        setWatchError(
          typeof message === 'string' ? message : '外部 Markdown 修改无效'
        )
        return
      }
      setWatchError(null)
      void engineRequest<WorkspaceState>(
        resyncRequired
          ? `/projects/${projectId}/open`
          : `/projects/${projectId}/workspace`,
        resyncRequired ? { method: 'POST' } : undefined
      )
        .then((current) => {
          if (!disposed) setWorkspace(current)
        })
        .catch((cause: unknown) => {
          if (!disposed) {
            setWatchError(
              cause instanceof Error ? cause.message : '工作区索引刷新失败'
            )
          }
        })
    })
      .then((unsubscribe) => {
        if (disposed) unsubscribe()
        else cleanup = unsubscribe
      })
      .catch((cause: unknown) => {
        if (!disposed) {
          setWatchError(
            cause instanceof Error ? cause.message : '工作区监听连接失败'
          )
        }
      })
    return () => {
      disposed = true
      cleanup()
    }
  }, [workspace?.project.project.id])

  async function closeProject() {
    if (!activeProjectId) return
    setWorkingProjectId(activeProjectId)
    setError(null)
    try {
      await engineRequest(`/projects/${activeProjectId}/close`, {
        method: 'POST',
      })
      setProjects((current) =>
        current.map((project) => ({ ...project, is_open: false }))
      )
      setWorkspace(null)
      clearActiveStoryProject()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '项目关闭失败')
    } finally {
      setWorkingProjectId(null)
    }
  }

  if (loading) {
    return (
      <StoryPage>
        <PageHeader eyebrow="Markdown Workspace" title="工作台" />
        <p
          className="border bg-background p-8 text-sm text-muted-foreground"
          role="status"
        >
          正在读取项目目录…
        </p>
      </StoryPage>
    )
  }

  if (!workspace) {
    return (
      <StoryPage>
        <PageHeader eyebrow="Markdown Workspace" title="工作台" />
        {error && (
          <p
            className="mb-4 bg-destructive/10 p-3 text-sm text-destructive"
            role="alert"
          >
            {error}
          </p>
        )}
        <section className="border bg-background">
          <div className="border-b px-5 py-4">
            <p className="text-xs text-muted-foreground">项目目录</p>
            <h2 className="mt-1 font-medium">选择一个项目开始工作</h2>
          </div>
          {projects.length === 0 ? (
            <div className="p-8 text-sm text-muted-foreground">
              尚无项目。先前往投稿页创建一个可运行的初始世界。
            </div>
          ) : (
            projects.map((project) => (
              <div
                className="flex flex-wrap items-center justify-between gap-4 border-b px-5 py-4 last:border-0"
                key={project.id}
              >
                <div>
                  <strong className="text-sm">{project.title}</strong>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {project.genre} · 世界版本 {project.world_version}
                  </p>
                </div>
                <Button
                  aria-label={`打开 ${project.title}`}
                  disabled={workingProjectId !== null}
                  onClick={() => setActiveStoryProjectId(project.id)}
                  size="sm"
                  variant="outline"
                >
                  <FolderOpen size={15} />
                  {workingProjectId === project.id ? '正在打开' : '打开'}
                </Button>
              </div>
            ))
          )}
        </section>
      </StoryPage>
    )
  }

  const snapshot = workspace.project
  const activeCharacters = snapshot.characters.filter(
    (character) => character.type === 'active'
  )
  const incident = snapshot.world.world_variables?.initial_incident
  const incidentLabel =
    typeof incident === 'string' ? incident : snapshot.world.active_pressures[0]

  return (
    <StoryPage>
      <PageHeader
        eyebrow={`${snapshot.project.title} / 世界版本 ${workspace.index.world_version}`}
        title="工作台"
        action={
          <div className="flex gap-2">
            <Button
              disabled={workingProjectId !== null}
              onClick={() => void closeProject()}
              size="sm"
              variant="outline"
            >
              关闭项目
            </Button>
            <Link className={primaryButton} to={route.evolve}>
              推进下一轮 <ArrowRight size={15} />
            </Link>
          </div>
        }
      />
      {(error || watchError || workspace.last_error) && (
        <p
          className="mb-4 flex items-center gap-2 bg-destructive/10 p-3 text-sm text-destructive"
          role="alert"
        >
          <TriangleAlert size={16} />
          {watchError || workspace.last_error || error}
        </p>
      )}
      {workspace.recovered_transactions > 0 && (
        <p
          className="mb-4 bg-emerald-500/10 p-3 text-sm text-emerald-700"
          role="status"
        >
          已恢复 {workspace.recovered_transactions} 个未完成的文件事务，并从
          Markdown 重建索引。
        </p>
      )}
      <section className="grid gap-6 border bg-background p-6 md:grid-cols-[1fr_auto]">
        <div className="max-w-2xl">
          <p className="mb-2 text-xs font-medium text-muted-foreground">
            当前世界
          </p>
          <h2 className="mb-3 font-studio text-xl font-medium">
            {incidentLabel || '当前世界已就绪'}
          </h2>
          <p className="leading-6 text-muted-foreground">
            {snapshot.world.active_pressures.join('；') ||
              '当前没有未解决的世界压力。'}
          </p>
        </div>
        <dl className="grid grid-cols-3 gap-px self-start overflow-hidden border bg-border text-center">
          {[
            ['时间', snapshot.world.current_time],
            ['地点', snapshot.world.current_location || '未指定'],
            ['压力', String(snapshot.world.active_pressures.length)],
          ].map(([label, value]) => (
            <div className="min-w-24 bg-background px-3 py-3" key={label}>
              <dt className="text-xs text-muted-foreground">{label}</dt>
              <dd className="mt-1 font-medium">{value}</dd>
            </div>
          ))}
        </dl>
      </section>
      <div className="mt-5 grid gap-5 lg:grid-cols-2">
        <section className="border bg-background">
          <div className="flex items-center justify-between border-b px-5 py-4">
            <div>
              <p className="text-xs text-muted-foreground">内存索引</p>
              <h2 className="font-medium">Canonical Markdown</h2>
            </div>
            <span className="grid size-7 place-items-center rounded-full bg-muted text-xs">
              {workspace.index.documents.length}
            </span>
          </div>
          {[
            [
              FileText,
              '初始事实',
              `${workspace.index.fact_ids.length} 条投稿事实`,
              '已索引',
            ],
            [
              FileText,
              '角色档案',
              `${Object.keys(workspace.index.character_versions).length} 个角色`,
              '已索引',
            ],
            [
              Check,
              '工作区索引',
              workspace.index.revision.slice(0, 12),
              '同步',
            ],
          ].map(([Icon, title, detail, state], index) => (
            <div
              className="grid grid-cols-[28px_1fr_auto] items-center gap-3 border-b px-5 py-4 last:border-0"
              key={String(title)}
            >
              <Icon
                className={
                  index === 0
                    ? 'text-amber-600'
                    : index === 2
                      ? 'text-emerald-600'
                      : 'text-muted-foreground'
                }
                size={16}
              />
              <div>
                <strong className="text-sm font-medium">{String(title)}</strong>
                <p className="mt-1 text-xs text-muted-foreground">
                  {String(detail)}
                </p>
              </div>
              <StatusPill tone={index === 2 ? 'success' : 'neutral'}>
                {String(state)}
              </StatusPill>
            </div>
          ))}
        </section>
        <section className="border bg-background">
          <div className="flex items-center justify-between border-b px-5 py-4">
            <div>
              <p className="text-xs text-muted-foreground">在场角色</p>
              <h2 className="font-medium">活跃角色</h2>
            </div>
            <Link
              className="text-xs font-medium text-primary"
              to={route.characters}
            >
              查看全部
            </Link>
          </div>
          {activeCharacters.map((character) => (
            <div
              className="grid grid-cols-[34px_1fr_auto] items-center gap-3 border-b px-5 py-4 last:border-0"
              key={character.id}
            >
              <span className="grid size-8 place-items-center rounded-full bg-muted text-xs font-medium">
                {(character.display_name || character.id).slice(0, 1)}
              </span>
              <div>
                <strong className="text-sm font-medium">
                  {character.display_name || character.id}
                </strong>
                <p className="mt-1 text-xs text-muted-foreground">
                  目标：{character.current_goal}
                </p>
              </div>
              <span className="text-xs text-muted-foreground">
                {character.location || '未指定'}
              </span>
            </div>
          ))}
        </section>
      </div>
    </StoryPage>
  )
}

export function SubmissionView() {
  const {
    draft,
    messages,
    composer,
    missingRequirements,
    reviewSummary,
    runnable,
    project,
    discussing,
    saving,
    error,
    setDraft,
    setMessages,
    setComposer,
    setMissingRequirements,
    setReviewSummary,
    setRunnable,
    setProject,
    setDiscussing,
    setSaving,
    setError,
  } = useSubmissionSession()
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
      if (useSubmissionSession.getState().project !== null) {
        resetSubmissionSession()
      }
    }
  }, [])
  const janMessages = useMemo<UIMessage[]>(
    () =>
      messages.map((message, index) => ({
        id: `submission-message-${index}`,
        role: message.role,
        parts: [{ type: 'text', text: message.content }],
      })),
    [messages]
  )
  const chatStatus: ChatStatus = discussing
    ? 'submitted'
    : error
      ? 'error'
      : 'ready'

  async function discuss() {
    const content = composer.trim()
    if (!content || discussing || project) return
    const nextMessages: SubmissionMessage[] = [
      ...messages,
      { role: 'user', content },
    ]
    setDiscussing(true)
    setError(null)
    setMessages(nextMessages)
    setComposer('')
    try {
      const response = await engineRequest<SubmissionConversationResponse>(
        `/projects/${draft.id}/submission/messages`,
        {
          method: 'POST',
          body: JSON.stringify({ draft, messages: nextMessages }),
        }
      )
      setDraft(response.draft)
      setMessages([
        ...nextMessages,
        { role: 'assistant', content: response.reply },
      ])
      setMissingRequirements(response.missing_requirements)
      setReviewSummary(response.review.summary)
      setRunnable(response.runnable)
    } catch (cause) {
      setMessages(messages)
      setComposer(content)
      setError(cause instanceof Error ? cause.message : '投稿讨论失败')
    } finally {
      setDiscussing(false)
    }
  }

  async function finalize() {
    if (!runnable) return
    setSaving(true)
    setError(null)
    try {
      const createdProject = await engineRequest<ProjectSnapshot>(
        '/submissions/finalize',
        {
          method: 'POST',
          body: JSON.stringify(draft satisfies SubmissionPackage),
        }
      )
      setActiveStoryProjectId(createdProject.project.id)
      if (mounted.current) {
        setProject(createdProject)
      } else {
        resetSubmissionSession()
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '投稿创建失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <StoryPage>
      <PageHeader
        eyebrow="投稿讨论 / 初始设定包"
        title="投稿"
        action={
          <Button
            disabled={saving || discussing || !runnable || project !== null}
            onClick={() => void finalize()}
            size="sm"
            type="button"
          >
            {project ? <Check size={15} /> : <LockKeyhole size={15} />}
            {project ? '项目已创建' : saving ? '正在创建' : '创建项目'}
          </Button>
        }
      />
      <div className="grid overflow-hidden border bg-background lg:grid-cols-[minmax(0,1.18fr)_minmax(360px,.82fr)]">
        <JanChatShell
          composer={
            <>
              <JanChatComposer
                ariaLabel="投稿消息"
                busy={discussing}
                disabled={discussing || project !== null}
                footer="Enter 发送 · Shift+Enter 换行"
                onSubmit={() => void discuss()}
                onValueChange={setComposer}
                placeholder="描述类型、主题、世界规则、人物或起始事件…"
                value={composer}
              />
              {error && (
                <p
                  className="mx-2 mt-2 rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive"
                  role="alert"
                >
                  {error}
                </p>
              )}
            </>
          }
          messages={janMessages}
          pendingLabel="正在整理设定包…"
          status={chatStatus}
          subtitle="从一个想法开始，不需要先写大纲"
          title="Story Editor"
        />
        <aside className="overflow-y-auto bg-muted/25 p-6">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs text-muted-foreground">Editor 整理结果</p>
              <h2 className="mt-1 font-studio text-2xl">
                {draft.title || '尚未命名'}
              </h2>
            </div>
            <StatusPill tone={runnable ? 'success' : 'warning'}>
              {runnable ? '设定包可运行' : '继续讨论'}
            </StatusPill>
          </div>
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            {reviewSummary}
          </p>
          <div className="mt-5 flex flex-wrap gap-2">
            {[draft.genre, draft.theme, draft.tone]
              .filter(Boolean)
              .map((item) => (
                <StatusPill key={item}>{item}</StatusPill>
              ))}
          </div>
          <dl className="mt-5 grid grid-cols-1 gap-px border bg-border text-xs sm:grid-cols-3">
            {[
              ['时间', draft.initial_time || '待讨论'],
              ['地点', draft.initial_location || '待讨论'],
              ['压力', draft.pressures[0] || '待讨论'],
            ].map(([label, value]) => (
              <div className="bg-background p-3" key={label}>
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="mt-1 font-medium">{value}</dd>
              </div>
            ))}
          </dl>
          <section className="mt-6 border-t pt-5">
            <h3 className="mb-3 text-xs font-medium text-muted-foreground">
              世界规则
            </h3>
            {draft.world_rules.length > 0 ? (
              <ul className="list-disc space-y-2 pl-5 text-sm">
                {draft.world_rules.map((rule) => (
                  <li key={rule}>{rule}</li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">
                尚未形成世界规则。
              </p>
            )}
          </section>
          <section className="mt-6 border-t pt-5">
            <h3 className="mb-3 flex items-center gap-2 text-xs font-medium text-muted-foreground">
              <UsersRound size={14} /> 初始角色
            </h3>
            {draft.characters.length > 0 ? (
              draft.characters.map((character) => (
                <article
                  className="border-b py-3 last:border-0"
                  key={character.id}
                >
                  <strong className="text-sm">{character.display_name}</strong>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {character.identity}
                  </p>
                  <small className="text-xs text-muted-foreground">
                    目标：{character.current_goal} · 私有事实{' '}
                    {character.known_fact_ids.length} 项
                  </small>
                </article>
              ))
            ) : (
              <p className="text-sm text-muted-foreground">
                需要 2–4 个初始角色。
              </p>
            )}
          </section>
          {!runnable && missingRequirements.length > 0 && (
            <section className="mt-6 border-t pt-5">
              <h3 className="mb-3 text-xs font-medium text-muted-foreground">
                仍需明确
              </h3>
              <ul className="space-y-2 text-sm text-amber-700 dark:text-amber-300">
                {missingRequirements.map((requirement) => (
                  <li key={requirement}>{requirement}</li>
                ))}
              </ul>
            </section>
          )}
          {project && (
            <div
              className="mt-5 flex flex-wrap items-center gap-3 bg-emerald-500/10 p-3 text-sm text-emerald-700"
              role="status"
            >
              <span className="flex items-center gap-2">
                <Check size={15} /> 世界版本 {project.world.version}
                ，可进入第一轮
              </span>
              <Link
                className="font-medium underline underline-offset-4"
                to={route.evolve}
              >
                进入第一轮
              </Link>
            </div>
          )}
        </aside>
      </div>
    </StoryPage>
  )
}

export { EvolutionView } from './evolution/EvolutionView'
function CharacterRelationshipGraph({
  characters,
}: {
  characters: StoryCharacter[]
}) {
  if (characters.length === 0) {
    return <p className="text-sm text-muted-foreground">暂无角色</p>
  }

  const width = 520
  const height = 420
  const radius = Math.min(width, height) / 2 - 52
  const nodes = characters.map((character, index) => {
    const angle = (index / characters.length) * Math.PI * 2 - Math.PI / 2
    return {
      character,
      x: width / 2 + radius * Math.cos(angle),
      y: height / 2 + radius * Math.sin(angle),
    }
  })
  const byId = new Map(nodes.map((node) => [node.character.id, node]))
  const seen = new Set<string>()
  const edges: Array<{
    from: (typeof nodes)[number]
    to: (typeof nodes)[number]
    label: string
  }> = []

  for (const node of nodes) {
    for (const relationship of node.character.relationships) {
      const target = byId.get(relationship.character_id)
      if (!target) continue
      const key = [node.character.id, target.character.id].sort().join('|')
      if (seen.has(key)) continue
      seen.add(key)
      edges.push({ from: node, to: target, label: relationship.description })
    }
  }

  function nodeClass(type: StoryCharacter['type']): string {
    if (type === 'active') return 'fill-emerald-600'
    if (type === 'npc') return 'fill-amber-500'
    return 'fill-muted-foreground'
  }

  return (
    <div className="border bg-background p-6">
      {edges.length === 0 && (
        <p className="mb-4 text-sm text-muted-foreground">暂无关系连线</p>
      )}
      <svg
        aria-label="角色关系图"
        className="h-auto w-full"
        height={height}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        {edges.map((edge) => (
          <g key={`${edge.from.character.id}-${edge.to.character.id}`}>
            <line
              className="text-border"
              stroke="currentColor"
              strokeWidth={1.5}
              x1={edge.from.x}
              x2={edge.to.x}
              y1={edge.from.y}
              y2={edge.to.y}
            />
            <title>{`${edge.from.character.display_name || edge.from.character.id} ↔ ${edge.to.character.display_name || edge.to.character.id}: ${edge.label}`}</title>
          </g>
        ))}
        {nodes.map((node) => {
          const name = node.character.display_name || node.character.id
          return (
            <g key={node.character.id}>
              <circle
                className={nodeClass(node.character.type)}
                cx={node.x}
                cy={node.y}
                r={30}
              />
              <text
                className="fill-neutral-50 text-xs font-medium"
                dominantBaseline="central"
                textAnchor="middle"
                x={node.x}
                y={node.y}
              >
                {name.slice(0, 4)}
              </text>
              <title>{name}</title>
            </g>
          )
        })}
      </svg>
      <div className="mt-5 flex flex-wrap gap-4 border-t pt-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-2">
          <span className="size-2.5 rounded-full bg-emerald-600" /> 活跃角色
        </span>
        <span className="flex items-center gap-2">
          <span className="size-2.5 rounded-full bg-amber-500" /> 普通人物
        </span>
        <span className="flex items-center gap-2">
          <span className="size-2.5 rounded-full bg-muted-foreground" /> 已退出
        </span>
      </div>
    </div>
  )
}

export function CharactersView() {
  const { branchId } = useBranchContext()
  const projectId = useActiveStoryProjectId()
  const [project, setProject] = useState<ProjectSnapshot | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [assessment, setAssessment] = useState<PromotionAssessment | null>(null)
  const [working, setWorking] = useState<'review' | 'promote' | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(projectId !== null)
  const [viewMode, setViewMode] = useState<'wiki' | 'roster' | 'relations'>('wiki')

  const loadProject = useCallback(async () => {
    if (!projectId) return
    const snapshot = await engineRequest<ProjectSnapshot>(
      `/projects/${projectId}`
    )
    setProject(snapshot)
    setSelectedId((current) =>
      snapshot.characters.some((character) => character.id === current)
        ? current
        : (snapshot.characters[0]?.id ?? null)
    )
  }, [projectId])

  useEffect(() => {
    if (!projectId) {
      setLoading(false)
      setProject(null)
      return
    }
    let disposed = false
    setLoading(true)
    void loadProject()
      .catch((cause: unknown) => {
        if (!disposed) {
          setError(cause instanceof Error ? cause.message : '角色读取失败')
        }
      })
      .finally(() => {
        if (!disposed) setLoading(false)
      })
    return () => {
      disposed = true
    }
  }, [loadProject, projectId])

  const current = useMemo<StoryCharacter | null>(
    () =>
      project?.characters.find((character) => character.id === selectedId) ??
      project?.characters[0] ??
      null,
    [project, selectedId]
  )

  async function reviewPromotion() {
    if (!projectId || !current) return
    setWorking('review')
    setError(null)
    setNotice(null)
    try {
      const result = await engineRequest<PromotionAssessment>(
        `/projects/${projectId}/characters/${current.id}/promotion-review?branch_id=${encodeURIComponent(branchId)}`,
        { method: 'POST' }
      )
      setAssessment(result)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '升级评估失败')
    } finally {
      setWorking(null)
    }
  }

  async function confirmPromotion() {
    if (!projectId || !current || !assessment?.candidate) return
    setWorking('promote')
    setError(null)
    try {
      const result = await engineRequest<PromotionCommitResult>(
        `/projects/${projectId}/characters/${current.id}/promote`,
        {
          method: 'POST',
          body: JSON.stringify({ candidate_id: assessment.candidate.id }),
        }
      )
      await loadProject()
      setAssessment(null)
      setNotice(
        `${result.character.display_name || result.character.id} 已升级为活跃角色`
      )
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '角色升级失败')
    } finally {
      setWorking(null)
    }
  }

  if (!projectId) {
    return (
      <StoryPage>
        <PageHeader eyebrow="角色工作区" title="角色" />
        <section className="border bg-background p-8">
          <h2 className="font-studio text-xl">尚未选择故事项目</h2>
          <Button asChild className="mt-5" variant="outline">
            <Link to={route.submission}>前往投稿</Link>
          </Button>
        </section>
      </StoryPage>
    )
  }

  if (loading || !project || !current) {
    return (
      <StoryPage>
        <PageHeader eyebrow="角色工作区" title="角色" />
        <p className="text-sm text-muted-foreground">正在读取角色档案...</p>
      </StoryPage>
    )
  }

  const activeCount = project.characters.filter(
    (character) => character.type === 'active'
  ).length
  return (
    <StoryPage>
      <PageHeader
        action={
          <StoryViewToggle
            label="角色视图"
            onChange={setViewMode}
            options={[
              { value: 'wiki', label: '角色 Wiki', icon: BookOpenText },
              { value: 'roster', label: '角色档案', icon: UsersRound },
              { value: 'relations', label: '关系图', icon: Network },
            ]}
            value={viewMode}
          />
        }
        eyebrow={`${activeCount} 个活跃角色`}
        title="角色"
      />
      {viewMode === 'wiki' ? (
        <CharacterWikiView
          branchId={branchId}
          characters={project.characters}
          projectId={projectId}
        />
      ) : viewMode === 'relations' ? (
        <CharacterRelationshipGraph characters={project.characters} />
      ) : (
      <div className="grid min-h-[540px] border bg-background md:grid-cols-[230px_1fr]">
        <nav className="border-b p-2 md:border-b-0 md:border-r">
          {project.characters.map((character) => (
            <Button
              className="mb-1 h-auto w-full justify-start px-3 py-3 text-left"
              key={character.id}
              onClick={() => {
                setSelectedId(character.id)
                setAssessment(null)
                setNotice(null)
              }}
              variant={character.id === current.id ? 'secondary' : 'ghost'}
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
        <article className="p-7">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-xs text-muted-foreground">角色私有档案</p>
              <h2 className="mt-1 font-studio text-2xl">
                {current.display_name || current.id}
              </h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {current.identity}
              </p>
            </div>
            <StatusPill
              tone={current.type === 'active' ? 'success' : 'neutral'}
            >
              {current.type === 'active' ? '活跃角色' : '普通人物'}
            </StatusPill>
          </div>
          <div className="mt-6 grid border md:grid-cols-2">
            {[
              ['核心欲望', current.core_desire],
              ['当前目标', current.current_goal || '尚未形成'],
              ['当前位置', current.location || '未知'],
              ['情绪状态', current.emotional_state || '未记录'],
            ].map(([label, value]) => (
              <section className="border-b p-5 odd:md:border-r" key={label}>
                <h3 className="text-xs text-muted-foreground">{label}</h3>
                <p className="mt-2 text-sm">{value}</p>
              </section>
            ))}
          </div>
          <section className="mt-6">
            <h3 className="mb-3 flex items-center gap-2 text-xs text-muted-foreground">
              <LockKeyhole size={14} /> 已知事实
            </h3>
            {current.known_fact_ids.length ? (
              <ul className="space-y-2">
                {current.known_fact_ids.map((fact) => (
                  <li
                    className="border-l-2 border-primary/40 pl-3 text-sm text-muted-foreground"
                    key={fact}
                  >
                    {fact}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">暂无已知事实</p>
            )}
          </section>
          <section className="mt-6">
            <h3 className="mb-3 flex items-center gap-2 text-xs text-muted-foreground">
              <UsersRound size={14} /> 关系
            </h3>
            {current.relationships.length ? (
              <ul className="space-y-2">
                {current.relationships.map((relationship) => {
                  const target = project.characters.find(
                    (character) => character.id === relationship.character_id
                  )
                  return (
                    <li
                      className="border-l-2 border-primary/40 pl-3 text-sm"
                      key={relationship.character_id}
                    >
                      <span className="font-medium">
                        {target?.display_name ||
                          target?.id ||
                          relationship.character_id}
                      </span>
                      <p className="mt-1 text-xs text-muted-foreground">
                        {relationship.description}
                      </p>
                    </li>
                  )
                })}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">暂无关系记录</p>
            )}
          </section>
          {current.type === 'npc' && (
            <section className="mt-7 border-t pt-5">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="text-xs text-muted-foreground">Editor</p>
                  <h3 className="mt-1 text-sm font-medium">角色升级建议</h3>
                </div>
                {!assessment?.candidate && (
                  <Button
                    disabled={working !== null}
                    onClick={() => void reviewPromotion()}
                    size="sm"
                    variant="outline"
                  >
                    <Sparkles size={15} />
                    {working === 'review' ? '正在评估' : '评估升级建议'}
                  </Button>
                )}
              </div>
              {assessment && (
                <div className="mt-4 border-l-2 border-primary/40 pl-4">
                  <p className="text-sm">{assessment.review.summary}</p>
                  {assessment.candidate && (
                    <>
                      <p className="mt-2 text-sm text-muted-foreground">
                        建议目标：{assessment.candidate.proposed_goal}
                      </p>
                      <Button
                        className="mt-4"
                        disabled={working !== null}
                        onClick={() => void confirmPromotion()}
                        size="sm"
                      >
                        <Check size={15} />
                        {working === 'promote'
                          ? '正在升级'
                          : '确认升级为活跃角色'}
                      </Button>
                    </>
                  )}
                </div>
              )}
            </section>
          )}
          {notice && (
            <p
              className="mt-5 bg-emerald-500/10 p-3 text-sm text-emerald-700"
              role="status"
            >
              {notice}
            </p>
          )}
          {error && (
            <p
              className="mt-5 bg-destructive/10 p-3 text-sm text-destructive"
              role="alert"
            >
              {error}
            </p>
          )}
        </article>
      </div>
      )}
    </StoryPage>
  )
}

export { WorldView } from './world/WorldView'

export { EventsView } from './events/EventsView'
export { ManuscriptView } from './manuscript/ManuscriptView'
