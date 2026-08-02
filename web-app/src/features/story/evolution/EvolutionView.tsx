import type { components } from '@story-engine/contracts'
import { Link } from '@tanstack/react-router'
import { ArrowRight, GitBranch, Pause, Play, Save, Square } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { route } from '@/constants/routes'
import { useActiveStoryProjectId } from '../activeProject'
import {
  PageHeader,
  primaryButton,
  StatusPill,
  StoryPage,
} from '../components/StoryLayout'
import { engineRequest } from '../engine'

type ProjectSnapshot = components['schemas']['ProjectSnapshot']
type SessionSnapshot = components['schemas']['TurnSessionSnapshot']
type StepResult = components['schemas']['StepResult']
type CommitResult = components['schemas']['CommitResult']

export function EvolutionView() {
  const projectId = useActiveStoryProjectId()
  const [project, setProject] = useState<ProjectSnapshot | null>(null)
  const [session, setSession] = useState<SessionSnapshot | null>(null)
  const [lastStep, setLastStep] = useState<StepResult | null>(null)
  const [premise, setPremise] = useState('')
  const [branchId, setBranchId] = useState('main')
  const [contentLocale, setContentLocale] = useState('zh-CN')
  const [forkId, setForkId] = useState('alternate')
  const [selectedActors, setSelectedActors] = useState<string[]>([])
  const [working, setWorking] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loadProject = useCallback(async () => {
    if (!projectId) return
    try {
      const loaded = await engineRequest<ProjectSnapshot>(`/projects/${projectId}`)
      const active = loaded.characters.filter((item) => item.type === 'active')
      setProject(loaded)
      setSelectedActors(active.map((item) => item.id))
      const incident = loaded.world.world_variables?.initial_incident
      setPremise(typeof incident === 'string' ? incident : '')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '项目加载失败')
    }
  }, [projectId])

  useEffect(() => {
    void loadProject()
  }, [loadProject])

  async function perform<T>(operation: () => Promise<T>): Promise<T | null> {
    setWorking(true)
    setError(null)
    try {
      return await operation()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '引擎请求失败')
      return null
    } finally {
      setWorking(false)
    }
  }

  async function start() {
    if (!projectId || !premise.trim()) return
    const created = await perform(() =>
      engineRequest<SessionSnapshot>(`/projects/${projectId}/simulations`, {
        method: 'POST',
        body: JSON.stringify({
          branch_id: branchId,
          premise_text: premise,
          actor_ids: selectedActors,
          content_locale: contentLocale,
          control: { mode: 'step', max_steps: 100 },
        }),
      })
    )
    if (created) {
      setSession(created)
      setLastStep(null)
      setNotice(`会话已启动：${created.session_id}`)
    }
  }

  async function step() {
    if (!projectId || !session) return
    const result = await perform(() =>
      engineRequest<StepResult>(
        `/projects/${projectId}/simulations/${session.session_id}/step`,
        { method: 'POST' }
      )
    )
    if (result) {
      setLastStep(result)
      setSession((current) =>
        current
          ? {
              ...current,
              current_step: result.step + 1,
              status: result.status,
              checkpoint_id: result.checkpoint_id,
            }
          : current
      )
    }
  }

  async function control(action: 'run' | 'pause' | 'resume' | 'terminate') {
    if (!projectId || !session) return
    const updated = await perform(() =>
      engineRequest<SessionSnapshot>(
        `/projects/${projectId}/simulations/${session.session_id}/${action}`,
        {
          method: 'POST',
          body:
            action === 'terminate'
              ? JSON.stringify({ reason_text: '用户终止会话' })
              : undefined,
        }
      )
    )
    if (updated) setSession(updated)
  }

  async function checkpoint() {
    if (!projectId || !session) return
    const committed = await perform(() =>
      engineRequest<CommitResult>(
        `/projects/${projectId}/simulations/${session.session_id}/checkpoint`,
        { method: 'POST', body: JSON.stringify({ reason: '用户检查点' }) }
      )
    )
    if (committed) {
      setSession((current) =>
        current ? { ...current, checkpoint_id: committed.checkpoint_id } : current
      )
      setNotice(`检查点已保存：${committed.checkpoint_id}`)
    }
  }

  async function fork() {
    if (!projectId || !session?.checkpoint_id) return
    const created = await perform(() =>
      engineRequest<components['schemas']['BranchManifest']>(
        `/projects/${projectId}/branches`,
        {
          method: 'POST',
          body: JSON.stringify({
            branch_id: forkId,
            source_checkpoint_id: session.checkpoint_id,
            parent_branch_id: session.branch_id,
            content_locale: session.content_locale,
          }),
        }
      )
    )
    if (created) setNotice(`分支已创建：${created.branch_id}`)
  }

  async function rebuildProjection() {
    if (!projectId || !session) return
    const result = await perform(() =>
      engineRequest<components['schemas']['ProjectionResponse']>(
        `/projects/${projectId}/branches/${session.branch_id}/projection`,
        {
          method: 'POST',
          body: JSON.stringify({ checkpoint_id: session.checkpoint_id }),
        }
      )
    )
    if (result) setNotice(`Markdown 投影已重建（${result.written_paths.length} 个文件）`)
  }

  async function switchLocale() {
    if (!projectId || !session) return
    const updated = await perform(() =>
      engineRequest<SessionSnapshot>(
        `/projects/${projectId}/simulations/${session.session_id}/locale`,
        {
          method: 'POST',
          body: JSON.stringify({ content_locale: contentLocale }),
        }
      )
    )
    if (updated) {
      setSession(updated)
      setNotice(`内容语言已切换为 ${updated.content_locale}`)
    }
  }

  if (!projectId) {
    return (
      <StoryPage>
        <PageHeader eyebrow="故事工作区" title="模拟控制台" />
        <section className="border bg-background p-8">
          <h2 className="font-studio text-xl">尚未选择故事项目</h2>
          <Link className={`${primaryButton} mt-5`} to={route.submission}>
            前往投稿 <ArrowRight size={15} />
          </Link>
        </section>
      </StoryPage>
    )
  }

  const activeCharacters =
    project?.characters.filter((character) => character.type === 'active') ?? []

  return (
    <StoryPage>
      <PageHeader
        eyebrow={project ? `${project.project.title} / ${branchId}` : '正在加载项目'}
        title="模拟控制台"
      />
      {error && (
        <p className="mb-4 border border-destructive p-3 text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="mb-4 bg-emerald-500/10 p-3 text-sm text-emerald-700" role="status">
          {notice}
        </p>
      )}

      {!session ? (
        <section className="space-y-5 border bg-background p-6">
          <div>
            <label className="text-sm font-medium" htmlFor="simulation-premise">
              场景目标
            </label>
            <Input
              id="simulation-premise"
              onChange={(event) => setPremise(event.target.value)}
              value={premise}
            />
          </div>
          <div>
            <label className="text-sm font-medium" htmlFor="content-locale">
              内容语言
            </label>
            <Input
              id="content-locale"
              onChange={(event) => setContentLocale(event.target.value)}
              value={contentLocale}
            />
          </div>
          <div>
            <label className="text-sm font-medium" htmlFor="simulation-branch">
              分支 ID
            </label>
            <Input
              id="simulation-branch"
              onChange={(event) => setBranchId(event.target.value)}
              value={branchId}
            />
          </div>
          <fieldset className="grid gap-2 sm:grid-cols-2">
            <legend className="mb-2 text-sm font-medium">参与角色</legend>
            {activeCharacters.map((character) => (
              <label className="flex items-center gap-2 border p-3 text-sm" key={character.id}>
                <input
                  checked={selectedActors.includes(character.id)}
                  onChange={() =>
                    setSelectedActors((current) =>
                      current.includes(character.id)
                        ? current.filter((id) => id !== character.id)
                        : [...current, character.id]
                    )
                  }
                  type="checkbox"
                />
                {character.display_name || character.id}
              </label>
            ))}
          </fieldset>
          <Button disabled={working || selectedActors.length === 0} onClick={() => void start()}>
            <Play size={15} /> 启动会话
          </Button>
        </section>
      ) : (
        <div className="space-y-5">
          <section className="border bg-background p-6">
            <div className="flex flex-wrap items-center gap-3">
              <StatusPill tone={session.status === 'failed' ? 'danger' : 'success'}>
                {session.status}
              </StatusPill>
              <span className="text-sm">Step {session.current_step}</span>
              <code className="text-xs text-muted-foreground">{session.checkpoint_id}</code>
            </div>
            <div className="mt-5 flex flex-wrap gap-2">
              <Button disabled={working} onClick={() => void step()}><Play size={15} /> 单步</Button>
              <Button disabled={working} onClick={() => void control('run')} variant="outline">连续运行</Button>
              <Button disabled={working} onClick={() => void control('pause')} variant="outline"><Pause size={15} /> 暂停</Button>
              <Button disabled={working} onClick={() => void control('resume')} variant="outline">继续</Button>
              <Button disabled={working} onClick={() => void checkpoint()} variant="outline"><Save size={15} /> 保存检查点</Button>
              <Button disabled={working} onClick={() => void control('terminate')} variant="destructive"><Square size={15} /> 终止</Button>
            </div>
          </section>

          {lastStep?.resolved_turn && (
            <section className="border bg-background p-6">
              <p className="text-xs text-muted-foreground">最近世界事件</p>
              <p className="mt-2">{lastStep.resolved_turn.raw_resolution_text}</p>
            </section>
          )}

          <section className="grid gap-3 border bg-background p-6 sm:grid-cols-[1fr_auto_auto]">
            <Input aria-label="新分支 ID" onChange={(event) => setForkId(event.target.value)} value={forkId} />
            <Button disabled={working || !session.checkpoint_id} onClick={() => void fork()} variant="outline"><GitBranch size={15} /> 从检查点分支</Button>
            <Button disabled={working} onClick={() => void rebuildProjection()} variant="outline">重建 Markdown</Button>
          </section>
          <section className="flex gap-3 border bg-background p-6">
            <Input
              aria-label="内容语言"
              onChange={(event) => setContentLocale(event.target.value)}
              value={contentLocale}
            />
            <Button disabled={working} onClick={() => void switchLocale()} variant="outline">
              切换内容语言
            </Button>
          </section>
        </div>
      )}
    </StoryPage>
  )
}
