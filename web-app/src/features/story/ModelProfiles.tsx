import type { components } from '@story-engine/contracts'
import { Link } from '@tanstack/react-router'
import { Check, ChevronDown, Loader2, RefreshCw, Settings2, Workflow } from 'lucide-react'
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
import { engineRequest } from './engine'

type UsageTotals = components['schemas']['UsageTotals']
type AgentType =
  | 'actor'
  | 'game_master'
  | 'writer'
  | 'editor'
  | 'wiki_maintainer'
  | 'submission_editor'
type ReasoningEffort =
  | 'none'
  | 'minimal'
  | 'low'
  | 'medium'
  | 'high'
  | 'xhigh'
  | 'max'
  | null
type AgentProfile = {
  name: string
  agent_type: AgentType
  default_system_prompt: string
  model: string | null
  reasoning_effort?: ReasoningEffort
  max_output_tokens: number | null
  temperature: number | null
  timeout_seconds: number
}
type CloudModelOption = { id: string; label: string }
type SaveStatus = 'idle' | 'saving' | 'saved' | 'error'

const emptyUsage: UsageTotals = {
  requests: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
  total_tokens: 0,
}

const reasoningEffortOptions: Array<{
  value: ReasoningEffort
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

function AgentProfileRow({
  profile,
  models,
  onSaved,
}: {
  profile: AgentProfile
  models: CloudModelOption[]
  onSaved: (profile: AgentProfile) => void
}) {
  const [draft, setDraft] = useState(profile)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [status, setStatus] = useState<SaveStatus>('idle')
  const [saveError, setSaveError] = useState<string | null>(null)
  const [temperatureText, setTemperatureText] = useState(
    String(profile.temperature ?? '')
  )
  const [tokensText, setTokensText] = useState(
    String(profile.max_output_tokens ?? '')
  )
  const [timeoutText, setTimeoutText] = useState(String(profile.timeout_seconds))
  const confirmedRef = useRef(profile)
  const pendingRef = useRef<Record<string, unknown>>({})
  const savingRef = useRef(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const saveRef = useRef<() => void>(() => {})

  useEffect(() => {
    setDraft(profile)
    confirmedRef.current = profile
    setTemperatureText(String(profile.temperature ?? ''))
    setTokensText(String(profile.max_output_tokens ?? ''))
    setTimeoutText(String(profile.timeout_seconds))
  }, [profile])

  useEffect(
    () => () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    },
    []
  )

  const save = useCallback(() => {
    if (savingRef.current || Object.keys(pendingRef.current).length === 0) return
    const patch = pendingRef.current
    pendingRef.current = {}
    savingRef.current = true
    setStatus('saving')
    setSaveError(null)
    engineRequest<AgentProfile>(`/agent-profiles/${profile.agent_type}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    })
      .then((saved) => {
        confirmedRef.current = saved
        setDraft(saved)
        setStatus('saved')
        onSaved(saved)
      })
      .catch((error) => {
        setDraft(confirmedRef.current)
        setTemperatureText(String(confirmedRef.current.temperature ?? ''))
        setTokensText(String(confirmedRef.current.max_output_tokens ?? ''))
        setTimeoutText(String(confirmedRef.current.timeout_seconds))
        setStatus('error')
        setSaveError(error instanceof Error ? error.message : 'Agent 设置更新失败')
      })
      .finally(() => {
        savingRef.current = false
        if (Object.keys(pendingRef.current).length > 0) saveRef.current()
      })
  }, [onSaved, profile.agent_type])
  saveRef.current = save

  const queue = useCallback((patch: Record<string, unknown>, debounce = false) => {
    pendingRef.current = { ...pendingRef.current, ...patch }
    setDraft((current) => ({ ...current, ...patch }) as AgentProfile)
    if (timerRef.current) clearTimeout(timerRef.current)
    if (debounce) timerRef.current = setTimeout(() => saveRef.current(), 400)
    else saveRef.current()
  }, [])

  const numeric = (
    field: 'temperature' | 'max_output_tokens' | 'timeout_seconds',
    text: string,
    min: number,
    max: number,
    nullable = false
  ) => {
    if (text === '' && nullable) {
      queue({ [field]: null }, true)
      return
    }
    const value = Number(text)
    if (Number.isFinite(value) && value >= min && value <= max) {
      queue({ [field]: value }, true)
    }
  }

  const selectedUnavailable =
    draft.model && !models.some((option) => option.id === draft.model)
  const prefix = `agent-${draft.agent_type}`

  return (
    <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
      <div className="p-3">
        <div className="grid min-w-0 grid-cols-1 items-end gap-3 lg:grid-cols-[minmax(11rem,0.8fr)_minmax(15rem,2fr)_auto]">
          <div className="min-w-0 space-y-1.5">
            <Label htmlFor={`${prefix}-name`}>Agent 名称</Label>
            <Input
              id={`${prefix}-name`}
              onChange={(event) => queue({ name: event.target.value }, true)}
              value={draft.name}
            />
            <span className="block text-xs text-muted-foreground">
              {draft.agent_type}
            </span>
          </div>
          <div className="min-w-0 space-y-1.5">
            <Label htmlFor={`${prefix}-model`}>{draft.name}模型</Label>
            <select
              aria-label={`${draft.name}模型`}
              className="h-9 w-full rounded-md border bg-background px-3 text-sm"
              id={`${prefix}-model`}
              onChange={(event) => queue({ model: event.target.value || null })}
              value={draft.model ?? ''}
            >
              <option value="">未选择模型</option>
              {selectedUnavailable && <option value={draft.model ?? ''}>{draft.model}（Provider 当前不可用）</option>}
              {models.map((model) => <option key={model.id} value={model.id}>{model.label}</option>)}
            </select>
          </div>
          <CollapsibleTrigger asChild>
            <Button aria-label={`${draft.name}高级参数`} size="icon-sm" variant="ghost">
              <ChevronDown className={cn('transition-transform', advancedOpen && 'rotate-180')} />
            </Button>
          </CollapsibleTrigger>
        </div>

        <CollapsibleContent>
          <div className="mt-3 space-y-3 border-t pt-3">
            <div className="space-y-1.5">
              <Label htmlFor={`${prefix}-prompt`}>默认系统提示词</Label>
              <textarea
                className="min-h-24 w-full resize-y rounded-md border bg-background px-3 py-2 text-sm"
                id={`${prefix}-prompt`}
                onChange={(event) => queue({ default_system_prompt: event.target.value }, true)}
                value={draft.default_system_prompt}
              />
            </div>
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              <div className="space-y-1.5">
                <Label htmlFor={`${prefix}-temperature`}>Temperature</Label>
                <Input id={`${prefix}-temperature`} max={2} min={0} onChange={(event) => { setTemperatureText(event.target.value); numeric('temperature', event.target.value, 0, 2, true) }} step={0.1} type="number" value={temperatureText} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${prefix}-reasoning`}>思考强度</Label>
                <select aria-label={`${draft.name}思考强度`} className="h-9 w-full rounded-md border bg-background px-3 text-sm" id={`${prefix}-reasoning`} onChange={(event) => queue({ reasoning_effort: event.target.value || null })} value={draft.reasoning_effort ?? ''}>
                  {reasoningEffortOptions.map((option) => <option key={option.label} value={option.value ?? ''}>{option.label}</option>)}
                </select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${prefix}-tokens`}>最大输出 Token</Label>
                <Input id={`${prefix}-tokens`} max={131072} min={1} onChange={(event) => { setTokensText(event.target.value); numeric('max_output_tokens', event.target.value, 1, 131072, true) }} placeholder="由供应商决定" type="number" value={tokensText} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${prefix}-timeout`}>超时（秒）</Label>
                <Input id={`${prefix}-timeout`} max={600} min={1} onChange={(event) => { setTimeoutText(event.target.value); numeric('timeout_seconds', event.target.value, 1, 600) }} type="number" value={timeoutText} />
              </div>
            </div>
          </div>
        </CollapsibleContent>

        <div className="mt-2 flex min-h-4 items-center gap-1.5 text-xs text-muted-foreground">
          {status === 'saving' && <><Loader2 className="size-3 animate-spin" />正在更新…</>}
          {status === 'saved' && <><Check className="size-3" />已更新</>}
          {status === 'error' && <span className="text-destructive">更新失败</span>}
        </div>
        {saveError && <p className="mt-2 text-xs text-destructive" role="alert">{saveError}</p>}
      </div>
    </Collapsible>
  )
}

export function ModelProfiles() {
  const providers = useModelProvider((state) => state.providers)
  const [profiles, setProfiles] = useState<AgentProfile[]>([])
  const [usage, setUsage] = useState<UsageTotals>(emptyUsage)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const cloudModels = useMemo<CloudModelOption[]>(
    () => providers
      .filter((provider) => provider.active && !isLocalProvider(provider.provider))
      .flatMap((provider) => provider.models.map((model) => ({ id: `${provider.provider}/${model.id}`, label: `${getProviderTitle(provider.provider)} · ${model.id}` })))
      .sort((left, right) => left.label.localeCompare(right.label)),
    [providers]
  )

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const [nextProfiles, nextUsage] = await Promise.all([
        engineRequest<AgentProfile[]>('/agent-profiles'),
        engineRequest<UsageTotals>('/models/usage').catch(() => emptyUsage),
      ])
      setProfiles(nextProfiles)
      setUsage(nextUsage)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : 'Agent 设置加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  if (loading) return <div aria-label="正在加载 Agent 设置" className="flex min-h-48 items-center justify-center"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>
  if (loadError) return <div className="flex min-h-32 items-center justify-between gap-3 border p-4"><p className="text-sm text-destructive" role="alert">{loadError}</p><Button onClick={() => void load()} size="sm" variant="outline"><RefreshCw />重新加载</Button></div>

  return (
    <div className="pb-6">
      <section className="p-4 text-muted-foreground">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <Workflow className="size-5 shrink-0 text-foreground" />
            <div><h2 className="text-base font-medium text-foreground">Agent 设置</h2><p className="text-xs">{usage.requests.toLocaleString()} 次调用 · {usage.total_tokens.toLocaleString()} Token</p></div>
          </div>
          <Button asChild size="sm" variant="outline"><Link to={route.settings.model_providers}><Settings2 />Provider 设置</Link></Button>
        </div>
        {cloudModels.length === 0 && <p className="mb-3 border border-dashed p-3 text-sm">当前没有可用的远程模型，请先启用 Provider 并添加模型。</p>}
        <div className="divide-y border bg-background">
          {profiles.map((profile) => <AgentProfileRow key={profile.agent_type} models={cloudModels} onSaved={(saved) => setProfiles((current) => current.map((item) => item.agent_type === saved.agent_type ? saved : item))} profile={profile} />)}
        </div>
      </section>
    </div>
  )
}
