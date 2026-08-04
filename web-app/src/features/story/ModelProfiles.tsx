import type { components } from '@story-engine/contracts'
import { Link } from '@tanstack/react-router'
import {
  Check,
  ChevronDown,
  Loader2,
  Plus,
  RefreshCw,
  Settings2,
  UsersRound,
  Workflow,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { route } from '@/constants/routes'
import { useModelProvider } from '@/hooks/useModelProvider'
import { cn, getProviderTitle, isLocalProvider } from '@/lib/utils'
import { useActiveStoryProjectId } from './activeProject'
import { engineRequest } from './engine'

type Character = components['schemas']['Character']
type ModelProfile = components['schemas']['ModelProfile']
type ModelTask = ModelProfile['task_type']
type ProjectModelPolicy = components['schemas']['ProjectModelPolicy']
type UsageTotals = components['schemas']['UsageTotals']

const taskLabels: Record<ModelTask, string> = {
  actor: '角色',
  game_master: '世界主持人',
  wiki_maintenance: 'Wiki 整理',
  editor: '审核',
  writer: '写作',
}
const defaultProfileIds: Record<ModelTask, string> = {
  actor: 'actor',
  game_master: 'game-master',
  wiki_maintenance: 'wiki-maintenance',
  editor: 'editor',
  writer: 'writer',
}
const emptyUsage: UsageTotals = {
  requests: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
  total_tokens: 0,
}
const reasoningEffortOptions: Array<{
  value: ModelProfile['reasoning_effort']
  label: string
}> = [
  { value: null, label: '跟随模型默认' },
  { value: 'none', label: '关闭思考' },
  { value: 'minimal', label: '最小' },
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
  { value: 'xhigh', label: '极高' },
  { value: 'max', label: '最大' },
]

type CloudModelOption = { id: string; label: string }
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

function profileLabel(profile: ModelProfile): string {
  const task = taskLabels[profile.task_type]
  return profile.id === defaultProfileIds[profile.task_type]
    ? `${task}模型`
    : `${task}模型（${profile.id}）`
}

function ProfileRow({
  profile,
  models,
  onSaved,
}: {
  profile: ModelProfile
  models: CloudModelOption[]
  onSaved: (profile: ModelProfile) => void
}) {
  const [draft, setDraft] = useState(profile)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [status, setStatus] = useState<SaveStatus>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [temperatureText, setTemperatureText] = useState(
    String(profile.temperature ?? '')
  )
  const [tokensText, setTokensText] = useState(String(profile.max_output_tokens))
  const [timeoutText, setTimeoutText] = useState(String(profile.timeout_seconds))
  const confirmedRef = useRef(profile)
  const pendingRef = useRef<Record<string, unknown>>({})
  const savingRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const saveRef = useRef<() => void>(() => {})

  useEffect(() => {
    setDraft(profile)
    pendingRef.current = {}
    const changed =
      JSON.stringify(profile) !== JSON.stringify(confirmedRef.current)
    confirmedRef.current = profile
    if (changed) setStatus('idle')
    setSaveError(null)
    setTemperatureText(String(profile.temperature ?? ''))
    setTokensText(String(profile.max_output_tokens))
    setTimeoutText(String(profile.timeout_seconds))
  }, [profile])

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    },
    []
  )

  const save = useCallback(() => {
    if (savingRef.current) return
    const patch = pendingRef.current
    if (Object.keys(patch).length === 0) return
    pendingRef.current = {}
    savingRef.current = true
    setStatus('saving')
    setSaveError(null)
    engineRequest<ModelProfile>(`/models/profiles/${profile.id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
      .then((saved) => {
        confirmedRef.current = saved
        setDraft(saved)
        setStatus('saved')
        onSaved(saved)
      })
      .catch((cause) => {
        setDraft(confirmedRef.current)
        setStatus('error')
        setSaveError(
          cause instanceof Error ? cause.message : '任务模型配置更新失败'
        )
      })
      .finally(() => {
        savingRef.current = false
        if (Object.keys(pendingRef.current).length > 0) saveRef.current()
      })
  }, [onSaved, profile.id])
  saveRef.current = save

  const queuePatch = useCallback((patch: Record<string, unknown>) => {
    pendingRef.current = { ...pendingRef.current, ...patch }
    setDraft((current) => ({ ...current, ...patch }) as ModelProfile)
    saveRef.current()
  }, [])

  const queueDebounced = useCallback((patch: Record<string, unknown>) => {
    pendingRef.current = { ...pendingRef.current, ...patch }
    setDraft((current) => ({ ...current, ...patch }) as ModelProfile)
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => saveRef.current(), 400)
  }, [])

  const commitNumeric = (
    field: 'temperature' | 'max_output_tokens' | 'timeout_seconds',
    text: string,
    min: number,
    max: number
  ) => {
    if (text === '') {
      if (field === 'temperature') queueDebounced({ temperature: null })
      return
    }
    const parsed = Number(text)
    if (!Number.isFinite(parsed) || parsed < min || parsed > max) return
    queueDebounced({ [field]: parsed })
  }

  const label = profileLabel(draft)
  const selectedUnavailable =
    draft.model_ref && !models.some((model) => model.id === draft.model_ref)

  return (
    <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
      <div className="p-3">
        <div className="grid min-w-0 grid-cols-1 items-end gap-3 lg:grid-cols-[minmax(9rem,0.8fr)_minmax(15rem,2fr)_auto]">
          <div className="self-center">
            <span className="block text-sm font-medium">{taskLabels[draft.task_type]}</span>
            <span className="mt-0.5 block truncate text-xs text-muted-foreground">
              {draft.id}
            </span>
          </div>
          <div className="min-w-0 space-y-1.5">
            <Label htmlFor={`${draft.id}-model`}>{label}</Label>
            <select
              aria-label={label}
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              id={`${draft.id}-model`}
              onChange={(event) =>
                queuePatch({ model_ref: event.target.value || null })
              }
              value={draft.model_ref ?? ''}
            >
              <option value="">未选择模型</option>
              {selectedUnavailable && (
                <option value={draft.model_ref ?? ''}>
                  {draft.model_ref}（Provider 当前不可用）
                </option>
              )}
              {models.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.label}
                </option>
              ))}
            </select>
          </div>
          <CollapsibleTrigger asChild>
            <Button aria-label={`${label}高级参数`} size="icon-sm" variant="ghost">
              <ChevronDown
                className={cn(
                  'transition-transform',
                  advancedOpen && 'rotate-180'
                )}
              />
            </Button>
          </CollapsibleTrigger>
        </div>

        <CollapsibleContent>
          <div className="mt-3 grid gap-3 border-t pt-3 sm:grid-cols-2 lg:grid-cols-4">
            <div className="space-y-1.5">
              <Label htmlFor={`${draft.id}-temperature`}>Temperature</Label>
              <Input
                id={`${draft.id}-temperature`}
                max={2}
                min={0}
                onChange={(event) => {
                  setTemperatureText(event.target.value)
                  commitNumeric('temperature', event.target.value, 0, 2)
                }}
                step={0.1}
                type="number"
                value={temperatureText}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`${draft.id}-reasoning`}>思考强度</Label>
              <select
                aria-label={`${label}思考强度`}
                className="h-9 w-full rounded-md border bg-background px-3 text-sm"
                id={`${draft.id}-reasoning`}
                onChange={(event) =>
                  queuePatch({
                    reasoning_effort:
                      (event.target.value ||
                        null) as ModelProfile['reasoning_effort'],
                  })
                }
                value={draft.reasoning_effort ?? ''}
              >
                {reasoningEffortOptions.map((option) => (
                  <option key={option.label} value={option.value ?? ''}>
                    {option.label}
                  </option>
                ))}
              </select>
            </div>
            {draft.task_type !== 'writer' && (
              <div className="space-y-1.5">
                <Label htmlFor={`${draft.id}-tokens`}>最大输出 Token</Label>
                <Input
                  id={`${draft.id}-tokens`}
                  max={8192}
                  min={1}
                  onChange={(event) => {
                    setTokensText(event.target.value)
                    commitNumeric('max_output_tokens', event.target.value, 1, 8192)
                  }}
                  type="number"
                  value={tokensText}
                />
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor={`${draft.id}-timeout`}>超时（秒）</Label>
              <Input
                id={`${draft.id}-timeout`}
                max={120}
                min={1}
                onChange={(event) => {
                  setTimeoutText(event.target.value)
                  commitNumeric('timeout_seconds', event.target.value, 1, 120)
                }}
                type="number"
                value={timeoutText}
              />
            </div>
          </div>
        </CollapsibleContent>
        <div className="mt-2 flex h-4 items-center gap-1.5 text-xs text-muted-foreground">
          {status === 'saving' && (
            <><Loader2 className="size-3 animate-spin" />正在更新…</>
          )}
          {status === 'saved' && (
            <><Check className="size-3" />已更新</>
          )}
          {status === 'error' && (
            <span className="text-destructive">更新失败</span>
          )}
        </div>
        {saveError && (
          <p className="mt-2 text-xs text-destructive" role="alert">
            {saveError}
          </p>
        )}
      </div>
    </Collapsible>
  )
}

function AgentProfileRow({
  character,
  actorProfiles,
  defaultProfileId,
  selectedProfileId,
  onSaved,
}: {
  character: Character
  actorProfiles: ModelProfile[]
  defaultProfileId: string
  selectedProfileId: string | null
  onSaved: (profileId: string | null) => Promise<void>
}) {
  const [draft, setDraft] = useState(selectedProfileId ?? '')
  const [status, setStatus] = useState<SaveStatus>('idle')
  const [error, setError] = useState<string | null>(null)
  const confirmedRef = useRef(selectedProfileId ?? '')
  const displayName = character.display_name || character.id

  useEffect(() => {
    const changed = (selectedProfileId ?? '') !== confirmedRef.current
    confirmedRef.current = selectedProfileId ?? ''
    setDraft(selectedProfileId ?? '')
    if (changed) setStatus('idle')
    setError(null)
  }, [selectedProfileId])

  const save = (value: string) => {
    if (value === confirmedRef.current) return
    const next = value || null
    setDraft(value)
    setStatus('saving')
    setError(null)
    onSaved(next)
      .then(() => {
        confirmedRef.current = value
        setStatus('saved')
      })
      .catch((caught) => {
        setDraft(confirmedRef.current)
        setStatus('error')
        setError(
          caught instanceof Error ? caught.message : 'Agent Profile 更新失败'
        )
      })
  }

  return (
    <div className="grid grid-cols-1 items-end gap-3 p-3 lg:grid-cols-[minmax(10rem,0.8fr)_minmax(15rem,2fr)_auto]">
      <div className="min-w-0 self-center">
        <span className="block truncate text-sm font-medium">{displayName}</span>
        <span className="block truncate text-xs text-muted-foreground">
          {character.id} · {character.type}
        </span>
      </div>
      <div className="min-w-0 space-y-1.5">
        <Label htmlFor={`agent-${character.id}`}>{displayName} Agent Profile</Label>
        <select
          aria-label={`${displayName} Agent Profile`}
          className="h-9 w-full rounded-md border bg-background px-3 text-sm"
          id={`agent-${character.id}`}
          onChange={(event) => save(event.target.value)}
          value={draft}
        >
          <option value="">跟随角色默认（{defaultProfileId}）</option>
          {actorProfiles.map((profile) => (
            <option key={profile.id} value={profile.id}>
              {profile.id} · {profile.model_ref ?? '未选择模型'}
            </option>
          ))}
          </select>
        </div>
      <div className="flex h-9 items-center gap-1.5 text-xs text-muted-foreground">
        {status === 'saving' && (
          <><Loader2 className="size-3 animate-spin" />正在更新…</>
        )}
        {status === 'saved' && (
          <><Check className="size-3" />已更新</>
        )}
        {status === 'error' && (
          <span className="text-destructive">更新失败</span>
        )}
      </div>
      {error && (
        <p className="text-xs text-destructive lg:col-start-2" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

export function ModelProfiles() {
  const providers = useModelProvider((state) => state.providers)
  const projectId = useActiveStoryProjectId()
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [usage, setUsage] = useState<UsageTotals>(emptyUsage)
  const [policy, setPolicy] = useState<ProjectModelPolicy | null>(null)
  const [characters, setCharacters] = useState<Character[]>([])
  const [creatingProfile, setCreatingProfile] = useState(false)
  const [createError, setCreateError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const cloudModels = useMemo<CloudModelOption[]>(
    () =>
      providers
        .filter((provider) => provider.active && !isLocalProvider(provider.provider))
        .flatMap((provider) =>
          provider.models.map((model) => ({
            id: `${provider.provider}/${model.id}`,
            label: `${getProviderTitle(provider.provider)} · ${model.id}`,
          }))
        )
        .sort((left, right) => left.label.localeCompare(right.label)),
    [providers]
  )
  const actorProfiles = profiles.filter((profile) => profile.task_type === 'actor')
  const taskProfileIds = policy?.task_profile_ids ?? defaultProfileIds
  const agentProfileIds = policy?.agent_profile_ids ?? {}

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const [nextProfiles, nextUsage, nextPolicy, nextCharacters] =
        await Promise.all([
          engineRequest<ModelProfile[]>('/models/profiles'),
          engineRequest<UsageTotals>('/models/usage').catch(() => emptyUsage),
          projectId
            ? engineRequest<ProjectModelPolicy>(
                `/projects/${projectId}/model-policy`
              ).catch(() => null)
            : Promise.resolve(null),
          projectId
            ? engineRequest<Character[]>(
                `/projects/${projectId}/characters`
              ).catch(() => [])
            : Promise.resolve([]),
        ])
      setProfiles(nextProfiles)
      setUsage(nextUsage)
      setPolicy(nextPolicy)
      setCharacters(nextCharacters)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '任务模型配置加载失败')
    } finally {
      setLoading(false)
    }
  }, [projectId])

  useEffect(() => {
    void load()
  }, [load])

  const saveAgentProfile = async (
    characterId: string,
    profileId: string | null
  ) => {
    if (!projectId || !policy) return
    const saved = await engineRequest<ProjectModelPolicy>(
      `/projects/${projectId}/model-policy`,
      {
        method: 'PATCH',
        body: JSON.stringify({
          agent_profile_ids: { [characterId]: profileId },
        }),
      }
    )
    setPolicy(saved)
  }

  const createActorProfile = async () => {
    if (creatingProfile) return
    const source =
      profiles.find((profile) => profile.id === (taskProfileIds.actor ?? 'actor')) ??
      actorProfiles[0]
    if (!source) {
      setCreateError('默认 Actor Profile 不存在')
      return
    }
    const existing = new Set(profiles.map((profile) => profile.id))
    let sequence = 1
    while (existing.has(`actor-custom-${sequence}`)) sequence += 1
    const draft: ModelProfile = {
      ...source,
      id: `actor-custom-${sequence}`,
      model_ref: null,
    }
    setCreatingProfile(true)
    setCreateError(null)
    try {
      const saved = await engineRequest<ModelProfile>(
        `/models/profiles/${draft.id}`,
        { method: 'PUT', body: JSON.stringify(draft) }
      )
      setProfiles((current) => [...current, saved])
    } catch (error) {
      setCreateError(error instanceof Error ? error.message : 'Actor Profile 创建失败')
    } finally {
      setCreatingProfile(false)
    }
  }

  if (loading) {
    return (
      <div
        aria-label="正在加载 Story Agent 模型"
        className="flex min-h-48 items-center justify-center"
      >
        <Loader2 className="size-5 animate-spin text-muted-foreground" />
      </div>
    )
  }

  if (loadError) {
    return (
      <div className="flex min-h-32 items-center justify-between gap-3 rounded-md border bg-card p-4">
        <p className="text-sm text-destructive" role="alert">{loadError}</p>
        <Button onClick={() => void load()} size="sm" variant="outline">
          <RefreshCw />重新加载
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-4 pb-6">
      <section className="bg-card p-4 text-muted-foreground">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <Workflow className="size-5 shrink-0 text-foreground" />
            <div>
              <h2 className="text-base font-medium text-foreground">任务 Profile</h2>
              <p className="text-xs">
                {usage.requests.toLocaleString()} 次调用 ·{' '}
                {usage.total_tokens.toLocaleString()} Token
              </p>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              disabled={creatingProfile}
              onClick={() => void createActorProfile()}
              size="sm"
              variant="outline"
            >
              {creatingProfile ? <Loader2 className="animate-spin" /> : <Plus />}
              新增角色 Profile
            </Button>
            <Button asChild size="sm" variant="outline">
              <Link to={route.settings.model_providers}>
                <Settings2 />Provider 设置
              </Link>
            </Button>
          </div>
        </div>
        {createError && (
          <p className="mb-3 text-sm text-destructive" role="alert">
            {createError}
          </p>
        )}
        {cloudModels.length === 0 && (
          <p className="mb-3 rounded-md border border-dashed p-3 text-sm">
            当前没有可用的远程模型，请先启用 Provider 并添加模型。
          </p>
        )}
        <div className="divide-y rounded-md border bg-background">
          {profiles.map((profile) => (
            <ProfileRow
              key={profile.id}
              models={cloudModels}
              onSaved={(saved) =>
                setProfiles((current) =>
                  current.map((item) => (item.id === saved.id ? saved : item))
                )
              }
              profile={profile}
            />
          ))}
        </div>
      </section>

      <section className="bg-card p-4 text-muted-foreground">
        <div className="mb-4 flex min-w-0 items-center gap-3">
          <UsersRound className="size-5 shrink-0 text-foreground" />
          <div className="min-w-0">
            <h2 className="text-base font-medium text-foreground">项目 Agent 覆写</h2>
            <p className="truncate text-xs">
              {projectId ? `当前项目：${projectId}` : '当前没有打开的 Story 项目'}
            </p>
          </div>
        </div>
        {!projectId ? (
          <p className="rounded-md border border-dashed p-3 text-sm">
            打开一个 Story 项目后，可在此为单个角色指定 Actor Profile。
          </p>
        ) : characters.length === 0 ? (
          <p className="rounded-md border border-dashed p-3 text-sm">
            当前项目没有可配置的角色。
          </p>
        ) : (
          <div className="divide-y rounded-md border bg-background">
            {characters.map((character) => (
              <AgentProfileRow
                actorProfiles={actorProfiles}
                character={character}
                defaultProfileId={taskProfileIds.actor ?? 'actor'}
                key={character.id}
                onSaved={(profileId) =>
                  saveAgentProfile(character.id, profileId)
                }
                selectedProfileId={agentProfileIds[character.id] ?? null}
              />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
