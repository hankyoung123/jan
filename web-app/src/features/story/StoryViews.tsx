import type { components } from '@story-engine/contracts'
import { Link } from '@tanstack/react-router'
import type { ChatStatus, UIMessage } from 'ai'
import {
  ArrowRight,
  Check,
  CheckCircle2,
  Download,
  FileText,
  FolderOpen,
  LockKeyhole,
  PanelRightClose,
  PanelRightOpen,
  RotateCcw,
  Save,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
  TriangleAlert,
  UsersRound,
} from 'lucide-react'
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import {
  JanChatComposer,
  JanChatShell,
} from '@/components/ai-elements/jan-chat-shell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { route } from '@/constants/routes'
import type { NovelManuscriptValue } from '@/editor/NovelManuscriptEditor'
import { novelDocumentFromMarkdown } from '@/editor/manuscriptMarkdown'
import {
  clearActiveStoryProject,
  setActiveStoryProjectId,
  useActiveStoryProjectId,
} from './activeProject'
import { engineRequest, subscribeProjectEvents } from './engine'

type SubmissionPackage = components['schemas']['SubmissionPackage']
type SubmissionDraft = components['schemas']['SubmissionDraft']
type SubmissionConversationResponse =
  components['schemas']['SubmissionConversationResponse']
type SubmissionMessage = components['schemas']['Message']
type ProjectSnapshot = components['schemas']['ProjectSnapshot']
type StoryCharacter = components['schemas']['Character']
type TurnCandidate = components['schemas']['TurnCandidate']
type TurnCancellationResult = components['schemas']['TurnCancellationResult']
type CommitResult = components['schemas']['CommitResult']
type StoryEvent = components['schemas']['StoryEvent']
type SceneDraft = components['schemas']['SceneDraft']
type SceneMutationResult = components['schemas']['SceneMutationResult']
type AmendmentCommitResult = components['schemas']['AmendmentCommitResult']
type ManuscriptExport = components['schemas']['ManuscriptExport']
type RagIndexSummary = components['schemas']['RagIndexSummary']
type ProjectCatalogEntry = components['schemas']['ProjectCatalogEntry']
type WorkspaceState = components['schemas']['WorkspaceState']
type PromotionAssessment = components['schemas']['PromotionAssessment']
type PromotionCommitResult = components['schemas']['PromotionCommitResult']

const NovelManuscriptEditor = lazy(() =>
  import('@/editor/NovelManuscriptEditor').then((module) => ({
    default: module.NovelManuscriptEditor,
  }))
)

const primaryButton =
  'inline-flex h-9 items-center justify-center gap-2 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground shadow-sm transition hover:brightness-95 disabled:pointer-events-none disabled:opacity-50'
const secondaryButton =
  'inline-flex h-9 items-center justify-center gap-2 rounded-md border bg-background px-3 text-sm font-medium transition hover:bg-accent disabled:pointer-events-none disabled:opacity-50'

function StoryPage({ children }: { children: ReactNode }) {
  return (
    <main className="h-svh overflow-y-auto bg-neutral-50 px-5 pb-12 pt-14 dark:bg-background md:px-8">
      <div className="mx-auto w-full max-w-6xl">{children}</div>
    </main>
  )
}

function PageHeader({
  eyebrow,
  title,
  action,
}: {
  eyebrow: string
  title: string
  action?: ReactNode
}) {
  return (
    <header className="mb-6 flex min-h-14 flex-wrap items-end justify-between gap-4 border-b pb-5">
      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">
          {eyebrow}
        </p>
        <h1 className="font-studio text-2xl font-medium">{title}</h1>
      </div>
      {action}
    </header>
  )
}

function StatusPill({
  children,
  tone = 'neutral',
}: {
  children: ReactNode
  tone?: 'success' | 'warning' | 'danger' | 'neutral'
}) {
  const tones = {
    success: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300',
    warning: 'bg-amber-500/12 text-amber-700 dark:text-amber-300',
    danger: 'bg-destructive/10 text-destructive',
    neutral: 'bg-muted text-muted-foreground',
  }
  return (
    <span
      className={`inline-flex rounded px-2 py-1 text-xs font-medium ${tones[tone]}`}
    >
      {children}
    </span>
  )
}

function chapterLabel(chapterId: string) {
  const match = /^chapter-(\d+)$/.exec(chapterId)
  if (!match) return chapterId
  return `第 ${Number(match[1])} 章`
}

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
      if (event.type !== 'workspace.changed') return
      if (event.payload.status === 'error') {
        const message = event.payload.error
        setWatchError(
          typeof message === 'string' ? message : '外部 Markdown 修改无效'
        )
        return
      }
      setWatchError(null)
      void engineRequest<WorkspaceState>(`/projects/${projectId}/workspace`)
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
              '正式事件',
              `${workspace.index.event_ids.length} 个不可变 Event`,
              '已索引',
            ],
            [
              FileText,
              '正文场景',
              `${workspace.index.scene_ids.length} 个 Scene`,
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

function newSubmissionDraft(): SubmissionDraft {
  return {
    id: `story-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    title: '',
    genre: '',
    theme: '',
    tone: '',
    world_rules: [],
    public_fact_ids: [],
    characters: [],
    initial_time: '',
    initial_location: '',
    initial_incident: '',
    pressures: [],
  }
}

const welcomeMessage: SubmissionMessage = {
  role: 'assistant',
  content:
    '告诉我你想建立怎样的故事世界。我们会一起明确创作方向、世界规则、初始角色和起始局面，不需要先写大纲。',
}

export function SubmissionView() {
  const [draft, setDraft] = useState<SubmissionDraft>(newSubmissionDraft)
  const [messages, setMessages] = useState<SubmissionMessage[]>([
    welcomeMessage,
  ])
  const [composer, setComposer] = useState('')
  const [missingRequirements, setMissingRequirements] = useState<string[]>([
    '创作方向',
    '世界规则与公共事实',
    '初始角色 (2-4 个)',
    '初始时间、地点和起始事件',
    '世界压力或角色目标冲突',
  ])
  const [reviewSummary, setReviewSummary] =
    useState('通过讨论逐步形成可运行的初始设定包。')
  const [runnable, setRunnable] = useState(false)
  const [project, setProject] = useState<ProjectSnapshot | null>(null)
  const [discussing, setDiscussing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
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
      setProject(createdProject)
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

export function EvolutionView() {
  const projectId = useActiveStoryProjectId()
  const [project, setProject] = useState<ProjectSnapshot | null>(null)
  const [loading, setLoading] = useState(projectId !== null)
  const [candidate, setCandidate] = useState<TurnCandidate | null>(null)
  const [committed, setCommitted] = useState<CommitResult | null>(null)
  const [revision, setRevision] = useState('让结果更克制')
  const [workingAction, setWorkingAction] = useState<
    'generate' | 'revise' | 'discard' | 'confirm' | null
  >(null)
  const [error, setError] = useState<string | null>(null)
  const [generationNotice, setGenerationNotice] = useState<string | null>(null)
  const [retryAvailable, setRetryAvailable] = useState(false)
  const working = workingAction !== null

  const loadProject = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    setError(null)
    try {
      setProject(await engineRequest<ProjectSnapshot>(`/projects/${projectId}`))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '项目加载失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void loadProject()
  }, [loadProject])

  async function act<Response>(
    action: NonNullable<typeof workingAction>,
    request: () => Promise<Response>
  ) {
    setWorkingAction(action)
    setError(null)
    try {
      return await request()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '引擎请求失败')
      return null
    } finally {
      setWorkingAction(null)
    }
  }
  const participants =
    project?.characters.filter((character) => character.type === 'active') ?? []
  const characterNames = new Map(
    project?.characters.map((character) => [
      character.id,
      character.display_name || character.id,
    ]) ?? []
  )
  const candidatePending =
    candidate !== null && candidate.status !== 'discarded' && committed === null

  async function generate() {
    if (!projectId || participants.length === 0 || candidatePending) return
    setWorkingAction('generate')
    setError(null)
    setGenerationNotice(null)
    try {
      const value = await engineRequest<TurnCandidate>(
        `/projects/${projectId}/turns/generate`,
        {
          method: 'POST',
          body: JSON.stringify({
            participant_ids: participants.map((character) => character.id),
          }),
        }
      )
      setCandidate(value)
      setCommitted(null)
      setRetryAvailable(false)
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : '本轮生成失败'
      if (message.includes('cancelled') || message.includes('取消')) {
        setGenerationNotice('本轮生成已取消，正式状态未改变。')
      } else {
        setError(message)
      }
      setRetryAvailable(true)
    } finally {
      setWorkingAction(null)
    }
  }
  async function cancelGeneration() {
    if (!projectId || workingAction !== 'generate') return
    setError(null)
    setGenerationNotice('正在取消本轮生成…')
    try {
      await engineRequest<TurnCancellationResult>(
        `/projects/${projectId}/turns/active/cancel`,
        { method: 'POST' }
      )
    } catch (cause) {
      setGenerationNotice(null)
      setError(cause instanceof Error ? cause.message : '取消本轮失败')
    }
  }
  async function revise() {
    if (!projectId || !candidate) return
    const value = await act('revise', () =>
      engineRequest<TurnCandidate>(
        `/projects/${projectId}/turns/${candidate.id}/request-revision`,
        { method: 'POST', body: JSON.stringify({ instruction: revision }) }
      )
    )
    if (value) setCandidate(value)
  }
  async function discard() {
    if (!projectId || !candidate) return
    const value = await act('discard', () =>
      engineRequest<TurnCandidate>(
        `/projects/${projectId}/turns/${candidate.id}/discard`,
        { method: 'POST' }
      )
    )
    if (value) setCandidate(value)
  }
  async function confirm() {
    if (!projectId || !candidate) return
    const value = await act('confirm', async () => {
      const result = await engineRequest<CommitResult>(
        `/projects/${projectId}/turns/${candidate.id}/confirm`,
        { method: 'POST' }
      )
      const refreshedProject = await engineRequest<ProjectSnapshot>(
        `/projects/${projectId}`
      )
      return { result, refreshedProject }
    })
    if (value) {
      setCandidate(value.result.candidate)
      setCommitted(value.result)
      setProject(value.refreshedProject)
    }
  }

  if (!projectId) {
    return (
      <StoryPage>
        <PageHeader eyebrow="故事工作区" title="推进故事" />
        <section className="border bg-background p-8">
          <h2 className="font-studio text-xl">尚未选择故事项目</h2>
          <p className="mt-2 text-sm text-muted-foreground">
            先完成投稿，Story Engine 才能建立第一版正式世界状态。
          </p>
          <Link className={`${primaryButton} mt-5`} to={route.submission}>
            前往投稿 <ArrowRight size={15} />
          </Link>
        </section>
      </StoryPage>
    )
  }

  if (loading) {
    return (
      <StoryPage>
        <PageHeader eyebrow="正在读取 Story Engine" title="推进故事" />
        <section className="border bg-background p-8 text-sm text-muted-foreground">
          正在加载项目…
        </section>
      </StoryPage>
    )
  }

  if (!project) {
    return (
      <StoryPage>
        <PageHeader eyebrow="项目不可用" title="推进故事" />
        <section className="border bg-background p-8">
          <p className="text-sm text-destructive" role="alert">
            {error || '项目加载失败'}
          </p>
          <Button
            className="mt-5"
            onClick={() => void loadProject()}
            variant="outline"
          >
            <RotateCcw size={15} /> 重试加载
          </Button>
        </section>
      </StoryPage>
    )
  }

  const incident =
    typeof project.world.world_variables?.initial_incident === 'string'
      ? project.world.world_variables.initial_incident
      : '等待角色根据当前世界状态采取行动'
  const progress = [
    ['当前局面', true],
    ['角色行动', candidate !== null],
    ['世界结算', candidate !== null],
    ['编辑检查', candidate?.review !== null && candidate?.review !== undefined],
    ['用户确认', committed !== null],
    ['正文', committed !== null],
  ] as const

  return (
    <StoryPage>
      <PageHeader
        eyebrow={`${project.project.title} / 世界版本 ${project.world.version}`}
        title="推进故事"
        action={
          <div className="flex items-center gap-2">
            <Button
              aria-busy={workingAction === 'generate'}
              disabled={
                working || candidatePending || participants.length === 0
              }
              onClick={() => void generate()}
              size="sm"
              variant="outline"
            >
              <Sparkles size={15} />{' '}
              {workingAction === 'generate'
                ? '正在生成'
                : candidatePending
                  ? '本轮待确认'
                  : retryAvailable
                    ? '重试本轮'
                    : '生成角色行动'}
            </Button>
            {workingAction === 'generate' && (
              <Button
                onClick={() => void cancelGeneration()}
                size="sm"
                variant="destructive"
              >
                <Square size={14} /> 取消生成
              </Button>
            )}
          </div>
        }
      />
      <ol
        aria-label="故事推进步骤"
        className="mb-5 grid grid-cols-2 gap-px overflow-hidden border bg-border text-xs sm:grid-cols-3 lg:grid-cols-6"
      >
        {progress.map(([label, complete], index) => (
          <li
            className="flex items-center gap-2 bg-background px-3 py-3"
            key={label}
          >
            <span
              className={`grid size-5 shrink-0 place-items-center rounded-full ${complete ? 'bg-primary text-primary-foreground' : 'bg-muted text-muted-foreground'}`}
            >
              {complete ? <Check size={12} /> : index + 1}
            </span>
            <span
              className={complete ? 'font-medium' : 'text-muted-foreground'}
            >
              {label}
            </span>
          </li>
        ))}
      </ol>
      {!candidate ? (
        <section className="border bg-background p-8">
          <p className="text-xs text-muted-foreground">当前局面</p>
          <h2 className="my-3 font-studio text-xl">{incident}</h2>
          <dl className="mb-4 grid max-w-2xl grid-cols-1 gap-px border bg-border text-sm sm:grid-cols-3">
            {[
              ['时间', project.world.current_time],
              ['地点', project.world.current_location || '未指定'],
              ['压力', project.world.active_pressures.join('；') || '暂无'],
            ].map(([label, value]) => (
              <div className="bg-background p-3" key={label}>
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="mt-1">{value}</dd>
              </div>
            ))}
          </dl>
          <p className="text-muted-foreground">
            {participants
              .map((character) => character.display_name || character.id)
              .join('、')}{' '}
            将依据各自的私有知识独立行动。
          </p>
        </section>
      ) : (
        <>
          <section className="border bg-background">
            <div className="flex items-center justify-between border-b px-5 py-4">
              <div>
                <p className="text-xs text-muted-foreground">
                  私有上下文已隔离
                </p>
                <h2 className="font-medium">角色行动</h2>
              </div>
              <StatusPill tone="success">
                {candidate.intents.length} / {candidate.intents.length} 完成
              </StatusPill>
            </div>
            {candidate.intents.map((intent) => (
              <div
                className="grid gap-3 border-b px-5 py-4 md:grid-cols-[22px_160px_1fr_auto] md:items-center"
                key={intent.character_id}
              >
                <CheckCircle2 className="text-emerald-600" size={17} />
                <div>
                  <strong className="text-sm">
                    {characterNames.get(intent.character_id) ||
                      intent.character_id}
                  </strong>
                  <p className="text-xs text-muted-foreground">{intent.goal}</p>
                </div>
                <p className="text-sm text-muted-foreground">{intent.action}</p>
                <StatusPill tone="success">已完成</StatusPill>
              </div>
            ))}
          </section>
          <section className="mt-5 grid gap-5 border bg-background p-5 md:grid-cols-[1fr_280px]">
            <div>
              <p className="text-xs text-muted-foreground">统一结算</p>
              <h2 className="my-2 font-studio text-lg">
                {candidate.outcome.summary}
              </h2>
              <p className="text-sm text-muted-foreground">
                {candidate.outcome.public_results.join(' · ')}
              </p>
            </div>
            <div className="border-t pt-5 md:border-l md:border-t-0 md:pl-5 md:pt-0">
              <p className="flex items-center gap-2 text-sm font-medium">
                <ShieldCheck
                  className={
                    candidate.review?.passed
                      ? 'text-emerald-600'
                      : 'text-destructive'
                  }
                  size={17}
                />{' '}
                Editor {candidate.review?.passed ? '检查通过' : '要求修订'}
              </p>
              <p className="mt-2 text-xs text-muted-foreground">
                {candidate.review?.summary}
              </p>
            </div>
          </section>
          {candidate.outcome.new_npcs.length > 0 && (
            <section className="mt-5 border bg-background">
              <div className="flex items-center justify-between border-b px-5 py-4">
                <div>
                  <p className="text-xs text-muted-foreground">Resolver 提议</p>
                  <h2 className="font-medium">新增普通人物</h2>
                </div>
                <StatusPill tone="warning">待用户确认后创建普通人物</StatusPill>
              </div>
              {candidate.outcome.new_npcs.map((npc) => (
                <div
                  className="grid gap-2 border-b px-5 py-4 last:border-0 md:grid-cols-[1fr_1fr]"
                  key={npc.id}
                >
                  <div>
                    <strong className="text-sm">{npc.identity}</strong>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {npc.id}
                    </p>
                  </div>
                  <p className="text-sm text-muted-foreground">{npc.purpose}</p>
                </div>
              ))}
            </section>
          )}
          {!committed && candidate.status !== 'discarded' && (
            <section
              aria-busy={working}
              className="mt-5 grid gap-3 border bg-background p-4 md:grid-cols-[1fr_auto_auto_auto]"
            >
              <Input
                aria-label="修改要求"
                value={revision}
                onChange={(event) => setRevision(event.target.value)}
              />
              <Button
                disabled={working || !revision.trim()}
                onClick={() => void revise()}
                variant="outline"
              >
                <RotateCcw size={15} />{' '}
                {workingAction === 'revise' ? '正在修改' : '要求修改'}
              </Button>
              <Button
                aria-label={
                  workingAction === 'discard' ? '正在放弃本轮' : '放弃本轮'
                }
                disabled={working}
                onClick={() => void discard()}
                size="icon"
                title="放弃本轮"
                variant="outline"
              >
                <Trash2 size={15} />
              </Button>
              <Button
                disabled={working || !candidate.review?.passed}
                onClick={() => void confirm()}
              >
                <Check size={15} />{' '}
                {workingAction === 'confirm' ? '正在提交' : '确认本轮'}
              </Button>
            </section>
          )}
          {committed && (
            <p
              className="mt-5 flex items-center gap-2 bg-emerald-500/10 p-3 text-sm text-emerald-700"
              role="status"
            >
              <CheckCircle2 size={16} /> {committed.event.id} 已写入正式
              Markdown
            </p>
          )}
          {candidate.status === 'discarded' && (
            <p
              className="mt-5 bg-muted p-3 text-sm text-muted-foreground"
              role="status"
            >
              本轮已放弃，正式状态未改变。
            </p>
          )}
        </>
      )}
      {generationNotice && (
        <p
          className="mt-4 bg-muted p-3 text-sm text-muted-foreground"
          role="status"
        >
          {generationNotice}
        </p>
      )}
      {error && (
        <p
          className="mt-4 bg-destructive/10 p-3 text-sm text-destructive"
          role="alert"
        >
          {error}
        </p>
      )}
    </StoryPage>
  )
}

export function CharactersView() {
  const projectId = useActiveStoryProjectId()
  const [project, setProject] = useState<ProjectSnapshot | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [assessment, setAssessment] = useState<PromotionAssessment | null>(null)
  const [working, setWorking] = useState<'review' | 'promote' | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(projectId !== null)

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
        `/projects/${projectId}/characters/${current.id}/promotion-review`,
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
      <PageHeader eyebrow={`${activeCount} 个活跃角色`} title="角色" />
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
    </StoryPage>
  )
}

export function WorldView() {
  const [saved, setSaved] = useState(true)
  return (
    <StoryPage>
      <PageHeader
        eyebrow={saved ? '所有更改已保存' : '存在未保存更改'}
        title="世界设定"
        action={
          <button
            className={secondaryButton}
            disabled={saved}
            onClick={() => setSaved(true)}
            title="保存世界设定"
            type="button"
          >
            <Save size={15} />
          </button>
        }
      />
      <div className="grid min-h-[560px] border bg-background md:grid-cols-[200px_1fr]">
        <nav className="flex overflow-x-auto border-b p-2 md:block md:border-b-0 md:border-r">
          {['创作方向', '世界规则', '地点', '历史', '组织', '研究资料'].map(
            (item, index) => (
              <button
                className={`min-w-max rounded-md px-3 py-2 text-left text-sm md:mb-1 md:w-full ${index === 1 ? 'bg-accent font-medium' : 'text-muted-foreground hover:bg-accent/60'}`}
                key={item}
                type="button"
              >
                {item}
              </button>
            )
          )}
        </nav>
        <article className="max-w-3xl p-8">
          <p className="text-xs text-muted-foreground">world.md / 世界规则</p>
          <input
            aria-label="文档标题"
            className="mt-2 w-full bg-transparent font-studio text-2xl outline-none"
            defaultValue="雾港运行规则"
            onChange={() => setSaved(false)}
          />
          <div className="my-6 h-0.5 w-10 bg-primary" />
          <h2 className="mb-3 font-medium">灯塔与夜航</h2>
          <textarea
            aria-label="世界规则正文"
            className="min-h-80 w-full resize-none bg-transparent text-base leading-7 outline-none"
            defaultValue={
              '灯塔控制雾港的夜间航路。暴风雨期间，所有进入近港航道的船只必须依赖灯塔主光源或港务所的备用航标。\n\n灯塔主光源由独立机械装置驱动，正常情况下不会因港区停电而熄灭。'
            }
            onChange={() => setSaved(false)}
          />
        </article>
      </div>
    </StoryPage>
  )
}

export function EventsView() {
  const events = [
    ['012', '22:12', '陈默在灯芯槽中发现新鲜刮痕', '陈默 · 灯塔一层'],
    ['011', '22:04', '林岚启动港务所备用航标', '林岚 · 港务所'],
    ['010', '21:58', '海燕号报告能见度降至三百米', '林岚 · 近港航道'],
    ['009', '21:51', '周放离开酒馆前往旧码头', '周放 · 旧码头'],
  ]
  return (
    <StoryPage>
      <PageHeader eyebrow="12 个已确认事件" title="事件历史" />
      <div className="max-w-4xl">
        {events.map(([sequence, time, title, detail], index) => (
          <article
            className="relative grid grid-cols-[48px_1fr] gap-4 pb-7"
            key={sequence}
          >
            {index < events.length - 1 && (
              <span className="absolute bottom-0 left-5 top-10 w-px bg-border" />
            )}
            <span className="z-10 grid size-10 place-items-center rounded-full border bg-background font-studio text-xs">
              {sequence}
            </span>
            <div className="border-b pb-6">
              <div className="mb-2 flex items-center gap-3 text-xs text-muted-foreground">
                <span>{time}</span>
                <StatusPill tone="success">已确认</StatusPill>
              </div>
              <h2 className="font-medium">{title}</h2>
              <p className="mt-2 text-sm text-muted-foreground">{detail}</p>
            </div>
          </article>
        ))}
      </div>
    </StoryPage>
  )
}

export function ManuscriptView() {
  const projectId = useActiveStoryProjectId()
  const [events, setEvents] = useState<StoryEvent[]>([])
  const [scenes, setScenes] = useState<SceneDraft[]>([])
  const [selectedSceneId, setSelectedSceneId] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [dirty, setDirty] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(true)
  const [loading, setLoading] = useState(projectId !== null)
  const [workingAction, setWorkingAction] = useState<
    'generate' | 'save' | 'confirm' | 'export' | 'rebuild' | null
  >(null)
  const [mutation, setMutation] = useState<SceneMutationResult | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const selectedScene = useMemo(
    () => scenes.find((scene) => scene.id === selectedSceneId) ?? null,
    [scenes, selectedSceneId]
  )
  const approvedEvents = useMemo(
    () =>
      events
        .filter((event) => event.approved_by_user)
        .sort((left, right) => left.sequence - right.sequence),
    [events]
  )
  const generationEvent = useMemo(() => {
    const represented = new Set(
      scenes.flatMap((scene) => scene.source_event_ids)
    )
    return (
      approvedEvents.filter((event) => !represented.has(event.id)).at(-1) ??
      null
    )
  }, [approvedEvents, scenes])
  const sourceEvents = useMemo(() => {
    if (!selectedScene) return []
    const ids = new Set(selectedScene.source_event_ids)
    return approvedEvents.filter((event) => ids.has(event.id))
  }, [approvedEvents, selectedScene])
  const chapters = useMemo(() => {
    const grouped = new Map<string, SceneDraft[]>()
    for (const scene of scenes) {
      const chapterScenes = grouped.get(scene.chapter_id) ?? []
      chapterScenes.push(scene)
      grouped.set(scene.chapter_id, chapterScenes)
    }
    return [...grouped].map(([id, chapterScenes]) => ({
      id,
      label: chapterLabel(id),
      scenes: chapterScenes,
    }))
  }, [scenes])
  const review = mutation?.review ?? selectedScene?.review ?? null
  const pendingAmendmentId =
    mutation?.amendment?.id ?? selectedScene?.amendment_id ?? null
  const initialContent = useMemo(() => novelDocumentFromMarkdown(body), [body])
  const canSaveGeneratedDraft =
    selectedScene?.status === 'reviewed' &&
    selectedScene.base_scene_version === 0 &&
    !dirty
  const canSaveScene =
    selectedScene !== null &&
    (dirty || canSaveGeneratedDraft) &&
    title.trim().length > 0 &&
    body.trim().length > 0

  function openScene(scene: SceneDraft) {
    setSelectedSceneId(scene.id)
    setTitle(scene.title)
    setBody(scene.body)
    setDirty(false)
    setMutation(null)
    setNotice(null)
    setError(null)
  }

  const loadWorkspace = useCallback(async () => {
    if (!projectId) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const [loadedEvents, loadedScenes] = await Promise.all([
        engineRequest<StoryEvent[]>(`/projects/${projectId}/events`),
        engineRequest<SceneDraft[]>(`/projects/${projectId}/scenes`),
      ])
      const orderedScenes = [...loadedScenes].sort(
        (left, right) => left.sequence - right.sequence
      )
      setEvents(loadedEvents)
      setScenes(orderedScenes)
      const latestScene = orderedScenes.at(-1)
      if (latestScene) {
        setSelectedSceneId(latestScene.id)
        setTitle(latestScene.title)
        setBody(latestScene.body)
      } else {
        setSelectedSceneId(null)
        setTitle('')
        setBody('')
      }
      setDirty(false)
      setMutation(null)
      setNotice(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '正文工作区加载失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void loadWorkspace()
  }, [loadWorkspace])

  function replaceScene(nextScene: SceneDraft) {
    setScenes((current) =>
      [...current.filter((scene) => scene.id !== nextScene.id), nextScene].sort(
        (left, right) => left.sequence - right.sequence
      )
    )
  }

  async function generateScene() {
    if (!projectId || !generationEvent || workingAction) return
    setWorkingAction('generate')
    setError(null)
    setNotice(null)
    try {
      const scene = await engineRequest<SceneDraft>(
        `/projects/${projectId}/scenes/generate`,
        {
          method: 'POST',
          body: JSON.stringify({
            event_ids: [generationEvent.id],
            chapter_id: selectedScene?.chapter_id ?? 'chapter-001',
          }),
        }
      )
      replaceScene(scene)
      openScene(scene)
      setNotice(`${scene.id} 已生成，请检查后保存`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '场景生成失败')
    } finally {
      setWorkingAction(null)
    }
  }

  async function saveScene() {
    if (
      !projectId ||
      !selectedScene ||
      (!dirty && !canSaveGeneratedDraft) ||
      !title.trim() ||
      !body.trim() ||
      workingAction
    ) {
      return
    }
    setWorkingAction('save')
    setError(null)
    setNotice(null)
    try {
      const result = await engineRequest<SceneMutationResult>(
        `/projects/${projectId}/scenes/${selectedScene.id}`,
        {
          method: 'PUT',
          body: JSON.stringify({
            title: title.trim(),
            body,
            expected_revision: selectedScene.revision,
            expected_scene_version: selectedScene.base_scene_version,
          }),
        }
      )
      replaceScene(result.draft)
      setTitle(result.draft.title)
      setBody(result.draft.body)
      setDirty(false)
      setMutation(result)
      if (result.status === 'saved') {
        setNotice('事实检查通过，正式 Markdown 已保存')
      } else if (result.status === 'amendment_required') {
        setNotice('检测到新事实，确认 Amendment 前正式正文不会改变')
      } else {
        setNotice('事实检查未通过，请根据审核结果修改正文')
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '正文保存失败')
    } finally {
      setWorkingAction(null)
    }
  }

  async function confirmAmendment() {
    if (!projectId || !selectedScene || !pendingAmendmentId || workingAction) {
      return
    }
    setWorkingAction('confirm')
    setError(null)
    setNotice(null)
    try {
      const result = await engineRequest<AmendmentCommitResult>(
        `/projects/${projectId}/scenes/${selectedScene.id}/amendments/${pendingAmendmentId}/confirm`,
        { method: 'POST' }
      )
      const refreshed = await engineRequest<SceneDraft>(
        `/projects/${projectId}/scenes/${selectedScene.id}`
      )
      replaceScene(refreshed)
      setEvents((current) =>
        current.some((event) => event.id === result.event.id)
          ? current
          : [...current, result.event]
      )
      setTitle(refreshed.title)
      setBody(refreshed.body)
      setDirty(false)
      setMutation(null)
      setNotice(`${result.event.id} 已确认，世界、事件与正式正文已原子写入`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Amendment 确认失败')
    } finally {
      setWorkingAction(null)
    }
  }

  async function exportManuscript() {
    if (!projectId || workingAction) return
    setWorkingAction('export')
    setError(null)
    try {
      const manuscript = await engineRequest<ManuscriptExport>(
        `/projects/${projectId}/manuscript/export`
      )
      const anchor = document.createElement('a')
      const createObjectUrl = URL.createObjectURL
      const objectUrl = createObjectUrl
        ? createObjectUrl(
            new Blob([manuscript.markdown], { type: 'text/markdown' })
          )
        : null
      anchor.href =
        objectUrl ??
        `data:text/markdown;charset=utf-8,${encodeURIComponent(manuscript.markdown)}`
      anchor.download = manuscript.filename
      anchor.hidden = true
      document.body.append(anchor)
      anchor.click()
      anchor.remove()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
      setNotice(`已导出 ${manuscript.filename}`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '正文导出失败')
    } finally {
      setWorkingAction(null)
    }
  }

  async function rebuildRetrievalIndex() {
    if (!projectId || workingAction) return
    setWorkingAction('rebuild')
    setError(null)
    setNotice(null)
    try {
      const result = await engineRequest<RagIndexSummary>(
        `/projects/${projectId}/rag/rebuild`,
        { method: 'POST' }
      )
      setNotice(`检索索引已重建：${result.chunk_count} 个片段`)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '检索索引重建失败')
    } finally {
      setWorkingAction(null)
    }
  }

  if (!projectId) {
    return (
      <StoryPage>
        <PageHeader eyebrow="正文工作区" title="章节正文" />
        <section className="grid min-h-80 place-items-center border bg-background p-8 text-center">
          <div>
            <h2 className="font-studio text-xl">尚未选择故事项目</h2>
            <p className="mt-2 text-sm text-muted-foreground">
              先通过投稿讨论创建项目，再从确认事件生成正文。
            </p>
            <Button asChild className="mt-5">
              <Link to={route.submission}>前往投稿</Link>
            </Button>
          </div>
        </section>
      </StoryPage>
    )
  }

  if (loading) {
    return (
      <StoryPage>
        <PageHeader eyebrow="正在读取事件与场景" title="章节正文" />
        <div className="grid min-h-80 place-items-center border bg-background text-sm text-muted-foreground">
          正在加载正文工作区…
        </div>
      </StoryPage>
    )
  }

  return (
    <StoryPage>
      <PageHeader
        action={
          <div className="flex flex-wrap items-center justify-end gap-2">
            <Button
              disabled={workingAction !== null}
              onClick={() => void exportManuscript()}
              type="button"
              variant="outline"
            >
              <Download />
              {workingAction === 'export' ? '正在导出' : '导出 Markdown'}
            </Button>
            <Button
              disabled={!generationEvent || workingAction !== null}
              onClick={() => void generateScene()}
              type="button"
              variant="outline"
            >
              <Sparkles />
              {workingAction === 'generate' ? '正在生成' : '从事件生成'}
            </Button>
            <Button
              disabled={!canSaveScene || workingAction !== null}
              onClick={() => void saveScene()}
              type="button"
            >
              <Save />
              {workingAction === 'save' ? '正在检查' : '保存并检查事实'}
            </Button>
          </div>
        }
        eyebrow={
          dirty
            ? '存在未保存更改'
            : selectedScene
              ? `${chapterLabel(selectedScene.chapter_id)} / 场景 ${String(selectedScene.sequence).padStart(3, '0')}`
              : `${approvedEvents.length} 个已确认事件`
        }
        title="章节正文"
      />
      <div
        className={`grid min-h-[620px] overflow-hidden border bg-background ${
          inspectorOpen
            ? 'lg:grid-cols-[210px_minmax(0,1fr)_280px]'
            : 'lg:grid-cols-[210px_minmax(0,1fr)_44px]'
        }`}
      >
        <aside className="border-b p-3 lg:border-b-0 lg:border-r">
          <strong className="px-2 text-sm">章节与场景</strong>
          <p className="mb-2 mt-3 px-2 text-xs text-muted-foreground">
            {scenes.length > 0 ? `${scenes.length} 个派生场景` : '尚未生成正文'}
          </p>
          <div className="flex gap-3 overflow-x-auto lg:block">
            {chapters.map((chapter) => (
              <section
                aria-label={chapter.label}
                className="min-w-44 lg:mb-4 lg:min-w-0"
                key={chapter.id}
              >
                <h2 className="mb-1 px-2 text-xs font-medium text-muted-foreground">
                  {chapter.label}
                </h2>
                <div className="flex gap-1 lg:block">
                  {chapter.scenes.map((scene) => (
                    <Button
                      aria-current={
                        selectedSceneId === scene.id ? 'page' : undefined
                      }
                      className={`mb-1 grid h-auto min-w-44 grid-cols-[32px_1fr] justify-start whitespace-normal rounded-md px-2 py-2 text-left lg:w-full lg:min-w-0 ${selectedSceneId === scene.id ? 'bg-accent text-foreground' : 'text-muted-foreground'}`}
                      disabled={dirty && selectedSceneId !== scene.id}
                      key={scene.id}
                      onClick={() => openScene(scene)}
                      type="button"
                      variant="ghost"
                    >
                      <span className="font-studio">
                        {String(scene.sequence).padStart(3, '0')}
                      </span>
                      <span>{scene.title}</span>
                    </Button>
                  ))}
                </div>
              </section>
            ))}
          </div>
        </aside>
        <article className="min-w-0 border-b lg:border-b-0">
          {selectedScene ? (
            <>
              <Input
                aria-label="场景标题"
                className="h-auto rounded-none border-x-0 border-t-0 bg-transparent px-8 py-6 font-studio text-2xl shadow-none focus-visible:ring-0 md:px-12 md:text-2xl"
                onChange={(event) => {
                  setTitle(event.target.value)
                  setDirty(true)
                  setMutation(null)
                  setNotice(null)
                }}
                value={title}
              />
              <Suspense
                fallback={
                  <div className="grid min-h-[500px] place-items-center text-sm text-muted-foreground">
                    正在加载 Novel 正文编辑器…
                  </div>
                }
              >
                <NovelManuscriptEditor
                  initialContent={initialContent}
                  key={selectedScene.id}
                  onChange={(value: NovelManuscriptValue) => {
                    setBody(value.markdown)
                    setDirty(true)
                    setMutation(null)
                    setNotice(null)
                  }}
                />
              </Suspense>
            </>
          ) : (
            <div className="grid min-h-[560px] place-items-center p-8 text-center">
              <div className="max-w-sm">
                <FileText className="mx-auto text-muted-foreground" />
                <h2 className="mt-4 font-studio text-xl">
                  从确认事件生成第一幕
                </h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  Writer 只读取已由用户确认的
                  Event，生成结果先进入派生场景，不会直接修改正式 Markdown。
                </p>
                {generationEvent && (
                  <p className="mt-4 text-sm">
                    下一来源：{generationEvent.id} · {generationEvent.summary}
                  </p>
                )}
              </div>
            </div>
          )}
        </article>
        <aside className={`lg:border-l ${inspectorOpen ? 'p-5' : 'p-1.5'}`}>
          <div
            className={`flex items-center ${inspectorOpen ? 'justify-between' : 'justify-center'}`}
          >
            {inspectorOpen && (
              <p className="text-xs text-muted-foreground">事实来源</p>
            )}
            <div className="flex items-center gap-0.5">
              {inspectorOpen && (
                <Button
                  aria-label="重建检索索引"
                  disabled={workingAction !== null}
                  onClick={() => void rebuildRetrievalIndex()}
                  size="icon-sm"
                  title="重建检索索引"
                  type="button"
                  variant="ghost"
                >
                  <RotateCcw />
                </Button>
              )}
              <Button
                aria-expanded={inspectorOpen}
                aria-label={inspectorOpen ? '收起事实来源' : '展开事实来源'}
                onClick={() => setInspectorOpen((open) => !open)}
                size="icon-sm"
                title={inspectorOpen ? '收起事实来源' : '展开事实来源'}
                type="button"
                variant="ghost"
              >
                {inspectorOpen ? <PanelRightClose /> : <PanelRightOpen />}
              </Button>
            </div>
          </div>
          {inspectorOpen && (
            <div>
              {sourceEvents.length > 0 ? (
                sourceEvents.map((event) => (
                  <article className="border-b py-3" key={event.id}>
                    <h2 className="text-sm font-medium">{event.id}</h2>
                    <p className="mt-1 text-sm leading-5 text-muted-foreground">
                      {event.summary}
                    </p>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {event.participants.join('、') || '无指定参与者'}
                    </p>
                  </article>
                ))
              ) : (
                <p className="mt-3 text-sm text-muted-foreground">
                  {selectedScene
                    ? '来源事件尚未载入。'
                    : '选择场景后显示来源。'}
                </p>
              )}
              {(selectedScene?.retrieval_evidence.length ?? 0) > 0 && (
                <section className="mt-5 border-t pt-5">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs text-muted-foreground">检索证据</p>
                    <span className="text-xs text-muted-foreground">
                      {selectedScene?.retrieval_evidence.length} 个片段
                    </span>
                  </div>
                  <div className="mt-2">
                    {selectedScene?.retrieval_evidence.map((evidence) => (
                      <article className="border-b py-3" key={`${evidence.task}:${evidence.chunk_id}`}>
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <h3 className="text-xs font-medium">
                            {evidence.task === 'writer'
                              ? 'Writer 证据'
                              : 'Editor 证据'}
                          </h3>
                          <span className="font-mono text-[11px] text-muted-foreground">
                            {evidence.score.toFixed(2)}
                          </span>
                        </div>
                        <p className="mt-1 break-all text-xs text-muted-foreground">
                          {evidence.source_id} · {evidence.heading}
                        </p>
                        <p className="mt-2 max-h-20 overflow-hidden text-xs leading-5">
                          {evidence.content}
                        </p>
                        <p className="mt-2 break-all font-mono text-[10px] text-muted-foreground">
                          {evidence.chunk_id} · {evidence.source_path} ·{' '}
                          {evidence.permission_scope}
                        </p>
                      </article>
                    ))}
                  </div>
                </section>
              )}
              {selectedScene && (
                <section className="mt-5 border-t pt-5">
                  <p className="text-xs text-muted-foreground">
                    Manuscript Review
                  </p>
                  <div
                    className={`my-3 flex items-start gap-2 p-3 text-xs ${
                      review?.review.passed
                        ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                        : review
                          ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300'
                          : 'bg-muted text-muted-foreground'
                    }`}
                  >
                    {review?.review.passed ? (
                      <ShieldCheck className="mt-0.5 shrink-0" size={15} />
                    ) : (
                      <TriangleAlert className="mt-0.5 shrink-0" size={15} />
                    )}
                    <span>
                      {review?.review.summary ??
                        '保存时由 Editor 检查事实差异。'}
                    </span>
                  </div>
                  {(review?.new_facts.length ?? 0) > 0 && (
                    <div>
                      <h3 className="text-xs font-medium">检测到的新事实</h3>
                      <ul className="mt-2 list-disc space-y-2 pl-4 text-sm text-muted-foreground">
                        {review?.new_facts.map((fact) => (
                          <li key={fact}>{fact}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {pendingAmendmentId && (
                    <div className="mt-5 border-t pt-4">
                      <p className="break-all text-xs text-muted-foreground">
                        {pendingAmendmentId}
                      </p>
                      <Button
                        className="mt-3 w-full"
                        disabled={workingAction !== null}
                        onClick={() => void confirmAmendment()}
                        type="button"
                        variant="outline"
                      >
                        <Check />
                        {workingAction === 'confirm'
                          ? '正在确认'
                          : '确认 Amendment'}
                      </Button>
                    </div>
                  )}
                </section>
              )}
            </div>
          )}
        </aside>
      </div>
      {notice && (
        <p
          className="mt-4 flex items-center gap-2 bg-primary/10 p-3 text-sm text-primary"
          role="status"
        >
          <CheckCircle2 size={16} /> {notice}
        </p>
      )}
      {error && (
        <div
          className="mt-4 flex flex-wrap items-center justify-between gap-3 bg-destructive/10 p-3 text-sm text-destructive"
          role="alert"
        >
          <span>{error}</span>
          <Button
            onClick={() => void loadWorkspace()}
            size="sm"
            type="button"
            variant="outline"
          >
            重试加载
          </Button>
        </div>
      )}
    </StoryPage>
  )
}
