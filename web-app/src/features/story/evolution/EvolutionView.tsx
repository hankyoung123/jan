import type { components } from '@story-engine/contracts'
import { Link } from '@tanstack/react-router'
import {
  ArrowRight,
  Check,
  CheckCircle2,
  MapPin,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  Square,
  Trash2,
} from 'lucide-react'
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
type StoryCharacter = components['schemas']['Character']
type TurnCandidate = components['schemas']['TurnCandidate']
type TurnCancellationResult = components['schemas']['TurnCancellationResult']
type CommitResult = components['schemas']['CommitResult']

function OfficeActivityPanel({
  characters,
  intents,
  generating,
}: {
  characters: StoryCharacter[]
  intents: Array<{ character_id: string; action: string }>
  generating: boolean
}) {
  if (characters.length === 0) return null
  const actionByCharacter = new Map(
    intents.map((intent) => [intent.character_id, intent.action])
  )
  return (
    <section aria-label="办公室活动" className="mb-5 border bg-background">
      <div className="flex items-center justify-between border-b px-5 py-4">
        <div>
          <p className="text-xs text-muted-foreground">办公室活动</p>
          <h2 className="font-medium">角色行动中</h2>
        </div>
        <StatusPill
          tone={
            generating
              ? 'warning'
              : intents.length > 0
                ? 'success'
                : 'neutral'
          }
        >
          {generating
            ? '生成中'
            : intents.length > 0
              ? '本轮已完成'
              : '等待行动'}
        </StatusPill>
      </div>
      <div className="grid gap-px border-t bg-border sm:grid-cols-2 lg:grid-cols-4">
        {characters.map((character) => {
          const action = actionByCharacter.get(character.id)
          return (
            <div className="bg-background p-4" key={character.id}>
              <div className="flex items-center gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-full bg-muted text-xs font-medium">
                  {(character.display_name || character.id).slice(0, 1)}
                </span>
                <div className="min-w-0">
                  <strong className="block truncate text-sm">
                    {character.display_name || character.id}
                  </strong>
                  <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
                    <MapPin size={12} />
                    {character.location || '未指定'}
                  </p>
                </div>
                <span
                  className={`ml-auto size-2 shrink-0 rounded-full ${
                    action ? 'animate-pulse bg-emerald-500' : 'bg-secondary'
                  }`}
                  title={action ? '行动中' : '等待行动'}
                />
              </div>
              <p className="mt-3 min-h-10 text-sm text-muted-foreground">
                {action ?? (generating ? '正在形成行动…' : '尚未行动')}
              </p>
            </div>
          )
        })}
      </div>
    </section>
  )
}

function recommendedParticipantIds(project: ProjectSnapshot): string[] {
  const active = project.characters.filter(
    (character) => character.type === 'active'
  )
  const recommended = active.find(
    (character) =>
      character.location !== null &&
      character.location === project.world.current_location
  )
  const recommendedId = recommended?.id ?? active[0]?.id
  return recommendedId ? [recommendedId] : []
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
  const [selectedParticipantIds, setSelectedParticipantIds] = useState<
    string[]
  >([])
  const working = workingAction !== null

  const loadProject = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    setError(null)
    try {
      const loaded = await engineRequest<ProjectSnapshot>(
        `/projects/${projectId}`
      )
      setSelectedParticipantIds(recommendedParticipantIds(loaded))
      setProject(loaded)
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
  const activeCharacters =
    project?.characters.filter((character) => character.type === 'active') ?? []
  const participants = activeCharacters.filter((character) =>
    selectedParticipantIds.includes(character.id)
  )
  const characterNames = new Map(
    project?.characters.map((character) => [
      character.id,
      character.display_name || character.id,
    ]) ?? []
  )
  const candidatePending =
    candidate !== null && candidate.status !== 'discarded' && committed === null

  function toggleParticipant(characterId: string) {
    setSelectedParticipantIds((current) =>
      current.includes(characterId)
        ? current.filter((id) => id !== characterId)
        : [...current, characterId]
    )
  }

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
      <OfficeActivityPanel
        characters={activeCharacters}
        generating={workingAction === 'generate'}
        intents={candidate?.intents ?? []}
      />
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
          <fieldset className="mb-4 max-w-2xl border p-3">
            <legend className="px-1 text-xs font-medium text-muted-foreground">
              本轮参与角色
            </legend>
            <div className="grid gap-2 sm:grid-cols-2">
              {activeCharacters.map((character) => (
                <label
                  className="flex items-center gap-3 border px-3 py-2 text-sm"
                  key={character.id}
                >
                  <input
                    aria-label={`选择 ${character.display_name || character.id}`}
                    checked={selectedParticipantIds.includes(character.id)}
                    disabled={working || candidatePending}
                    onChange={() => toggleParticipant(character.id)}
                    type="checkbox"
                  />
                  <span>
                    {character.display_name || character.id}
                    <span className="ml-2 text-xs text-muted-foreground">
                      {character.location || '位置未知'}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
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
                {candidate.outcome.fact_candidates
                  .filter((fact) => fact.visibility === 'public')
                  .map((fact) => fact.statement)
                  .join(' · ')}
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


