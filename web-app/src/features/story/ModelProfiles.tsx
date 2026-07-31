import type { components } from '@story-engine/contracts'
import { Link } from '@tanstack/react-router'
import {
  Check,
  ChevronDown,
  Loader2,
  RefreshCw,
  Save,
  Settings2,
  SlidersHorizontal,
  Workflow,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { Button } from '@/components/ui/button'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { route } from '@/constants/routes'
import { ModelCombobox } from '@/containers/ModelCombobox'
import ProvidersAvatar from '@/containers/ProvidersAvatar'
import { useModelProvider } from '@/hooks/useModelProvider'
import { cn, getProviderTitle } from '@/lib/utils'
import { engineRequest } from './engine'

type ModelProfile = components['schemas']['ModelProfile']
type UsageTotals = components['schemas']['UsageTotals']

const taskLabels: Record<ModelProfile['task_type'], string> = {
  character: '角色',
  resolver: '裁决',
  editor: '审核',
  writer: '写作',
  embedding: '嵌入',
}

const emptyUsage: UsageTotals = {
  requests: 0,
  prompt_tokens: 0,
  completion_tokens: 0,
  total_tokens: 0,
}

function ProfileRow({
  profile,
  providers,
  onSaved,
}: {
  profile: ModelProfile
  providers: ModelProvider[]
  onSaved: (profile: ModelProfile) => void
}) {
  const [draft, setDraft] = useState(profile)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    setDraft(profile)
    setSaveError(null)
  }, [profile])

  const provider = providers.find(
    (candidate) => candidate.provider === draft.provider_id
  )
  const models = useMemo(
    () =>
      Array.from(
        new Set((provider?.models ?? []).map((model) => model.id))
      ).sort((left, right) => left.localeCompare(right)),
    [provider]
  )
  const providerOptions = useMemo(() => {
    const active = providers.filter((candidate) => candidate.active)
    if (provider && !active.includes(provider)) active.push(provider)
    return active.sort((left, right) =>
      getProviderTitle(left.provider).localeCompare(
        getProviderTitle(right.provider)
      )
    )
  }, [provider, providers])

  const dirty = JSON.stringify(draft) !== JSON.stringify(profile)
  const valid =
    draft.provider_id.trim().length > 0 &&
    draft.model.trim().length > 0 &&
    draft.max_output_tokens >= 1 &&
    draft.max_output_tokens <= 8192 &&
    draft.timeout_seconds >= 1 &&
    draft.timeout_seconds <= 120 &&
    (draft.temperature === null ||
      draft.temperature === undefined ||
      (draft.temperature >= 0 && draft.temperature <= 2))

  const selectProvider = (providerId: string) => {
    const nextProvider = providers.find(
      (candidate) => candidate.provider === providerId
    )
    setDraft((current) => ({
      ...current,
      provider_id: providerId,
      model:
        nextProvider?.models.some((model) => model.id === current.model)
          ? current.model
          : (nextProvider?.models[0]?.id ?? ''),
    }))
    setSaveError(null)
  }

  const save = async () => {
    if (!dirty || !valid || saving) return
    setSaving(true)
    setSaveError(null)
    try {
      const saved = await engineRequest<ModelProfile>(
        `/models/profiles/${draft.id}`,
        {
          method: 'PUT',
          body: JSON.stringify(draft),
        }
      )
      onSaved(saved)
    } catch (error) {
      setSaveError(
        error instanceof Error ? error.message : '任务模型配置保存失败'
      )
    } finally {
      setSaving(false)
    }
  }

  const taskLabel = taskLabels[draft.task_type]

  return (
    <Collapsible open={advancedOpen} onOpenChange={setAdvancedOpen}>
      <div className="p-3">
        <div className="grid min-w-0 grid-cols-1 items-end gap-3 lg:grid-cols-[minmax(7rem,0.7fr)_minmax(8rem,1fr)_minmax(10rem,1.45fr)_auto_auto]">
          <div className="min-w-0 self-center">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-medium text-foreground">
                {taskLabel}
              </span>
              {!draft.enabled && (
                <span className="text-xs text-muted-foreground">已停用</span>
              )}
            </div>
            <div className="mt-1 flex h-4 items-center gap-1.5 text-xs text-muted-foreground">
              {saving ? (
                <>
                  <Loader2 className="size-3 animate-spin" /> 保存中
                </>
              ) : dirty ? (
                '未保存'
              ) : (
                <>
                  <Check className="size-3" /> 已保存
                </>
              )}
            </div>
          </div>

          <div className="min-w-0 space-y-1.5">
            <Label htmlFor={`${draft.id}-provider`}>Provider</Label>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  id={`${draft.id}-provider`}
                  variant="outline"
                  className="h-9 w-full min-w-0 justify-between rounded-md px-3 font-normal"
                  aria-label={`${taskLabel} Provider`}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    {provider && <ProvidersAvatar provider={provider} />}
                    <span className="truncate">
                      {getProviderTitle(draft.provider_id)}
                    </span>
                  </span>
                  <ChevronDown className="size-4 text-muted-foreground" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="start" className="min-w-48">
                {providerOptions.length === 0 ? (
                  <DropdownMenuItem disabled>
                    没有已启用的 Provider
                  </DropdownMenuItem>
                ) : (
                  <DropdownMenuRadioGroup
                    value={draft.provider_id}
                    onValueChange={selectProvider}
                  >
                    {providerOptions.map((option) => (
                      <DropdownMenuRadioItem
                        key={option.provider}
                        value={option.provider}
                      >
                        <ProvidersAvatar provider={option} />
                        <span>{getProviderTitle(option.provider)}</span>
                        {!option.active && (
                          <span className="ml-auto text-xs text-muted-foreground">
                            已停用
                          </span>
                        )}
                      </DropdownMenuRadioItem>
                    ))}
                  </DropdownMenuRadioGroup>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </div>

          <div className="min-w-0 space-y-1.5">
            <Label htmlFor={`${draft.id}-model`}>模型</Label>
            <ModelCombobox
              inputId={`${draft.id}-model`}
              value={draft.model}
              onChange={(model) => {
                setDraft((current) => ({ ...current, model }))
                setSaveError(null)
              }}
              models={models}
              placeholder="选择或输入模型 ID"
              className="w-full"
            />
          </div>

          <div className="flex h-9 items-center gap-2 self-end lg:justify-center">
            <Switch
              id={`${draft.id}-enabled`}
              checked={draft.enabled}
              onCheckedChange={(enabled) =>
                setDraft((current) => ({ ...current, enabled }))
              }
            />
            <Label htmlFor={`${draft.id}-enabled`} className="lg:sr-only">
              启用{taskLabel}
            </Label>
          </div>

          <div className="flex h-9 items-center gap-1 self-end">
            <Tooltip>
              <CollapsibleTrigger asChild>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={`${taskLabel}高级设置`}
                  >
                    <SlidersHorizontal
                      className={cn(
                        'text-muted-foreground transition-colors',
                        advancedOpen && 'text-foreground'
                      )}
                    />
                  </Button>
                </TooltipTrigger>
              </CollapsibleTrigger>
              <TooltipContent>高级设置</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  size="icon-sm"
                  disabled={!dirty || !valid || saving}
                  onClick={() => void save()}
                  aria-label={`保存${taskLabel}配置`}
                >
                  {saving ? (
                    <Loader2 className="animate-spin" />
                  ) : (
                    <Save />
                  )}
                </Button>
              </TooltipTrigger>
              <TooltipContent>保存配置</TooltipContent>
            </Tooltip>
          </div>
        </div>

        <CollapsibleContent>
          <div className="mt-3 grid grid-cols-1 gap-3 border-t pt-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor={`${draft.id}-temperature`}>Temperature</Label>
              <Input
                id={`${draft.id}-temperature`}
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={draft.temperature ?? ''}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    temperature:
                      event.target.value === ''
                        ? null
                        : Number(event.target.value),
                  }))
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`${draft.id}-tokens`}>最大输出 Token</Label>
              <Input
                id={`${draft.id}-tokens`}
                type="number"
                min={1}
                max={8192}
                step={1}
                value={draft.max_output_tokens}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    max_output_tokens: Number(event.target.value),
                  }))
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor={`${draft.id}-timeout`}>超时（秒）</Label>
              <Input
                id={`${draft.id}-timeout`}
                type="number"
                min={1}
                max={120}
                step={1}
                value={draft.timeout_seconds}
                onChange={(event) =>
                  setDraft((current) => ({
                    ...current,
                    timeout_seconds: Number(event.target.value),
                  }))
                }
              />
            </div>
          </div>
        </CollapsibleContent>

        {saveError && (
          <p role="alert" className="mt-2 text-xs text-destructive">
            {saveError}
          </p>
        )}
      </div>
    </Collapsible>
  )
}

export function ModelProfiles() {
  const providers = useModelProvider((state) => state.providers)
  const [profiles, setProfiles] = useState<ModelProfile[]>([])
  const [usage, setUsage] = useState<UsageTotals>(emptyUsage)
  const [open, setOpen] = useState(true)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError(null)
    try {
      const [nextProfiles, nextUsage] = await Promise.all([
        engineRequest<ModelProfile[]>('/models/profiles'),
        engineRequest<UsageTotals>('/models/usage').catch(() => emptyUsage),
      ])
      setProfiles(nextProfiles)
      setUsage(nextUsage)
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : '任务模型配置加载失败'
      )
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const updateSavedProfile = (saved: ModelProfile) => {
    setProfiles((current) =>
      current.map((profile) => (profile.id === saved.id ? saved : profile))
    )
  }

  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="shrink-0 border-b bg-muted/20"
    >
      <div className="mx-auto w-full px-4 py-3 md:w-4/5 xl:w-4/6">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-3">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-background">
              <Workflow className="size-4 text-muted-foreground" />
            </div>
            <div className="min-w-0">
              <h2 className="truncate text-sm font-medium text-foreground">
                任务模型
              </h2>
              <p className="truncate text-xs text-muted-foreground">
                {usage.requests.toLocaleString()} 次调用 ·{' '}
                {usage.total_tokens.toLocaleString()} Token
              </p>
            </div>
          </div>

          <div className="flex items-center gap-1">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="icon-sm" asChild>
                  <Link
                    to={route.settings.model_providers}
                    aria-label="打开 Provider 设置"
                  >
                    <Settings2 />
                  </Link>
                </Button>
              </TooltipTrigger>
              <TooltipContent>Provider 设置</TooltipContent>
            </Tooltip>
            <Tooltip>
              <CollapsibleTrigger asChild>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={open ? '收起任务模型' : '展开任务模型'}
                  >
                    <ChevronDown
                      className={cn(
                        'transition-transform',
                        open && 'rotate-180'
                      )}
                    />
                  </Button>
                </TooltipTrigger>
              </CollapsibleTrigger>
              <TooltipContent>{open ? '收起' : '展开'}</TooltipContent>
            </Tooltip>
          </div>
        </div>

        <CollapsibleContent className="max-h-[48vh] overflow-y-auto">
          <div className="pt-3">
            {loading ? (
              <div
                className="flex h-24 items-center justify-center text-muted-foreground"
                aria-label="正在加载任务模型"
              >
                <Loader2 className="size-4 animate-spin" />
              </div>
            ) : loadError ? (
              <div className="flex min-h-24 items-center justify-between gap-3 rounded-md border bg-background px-3 py-2">
                <p role="alert" className="min-w-0 text-sm text-destructive">
                  {loadError}
                </p>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="outline"
                      size="icon-sm"
                      onClick={() => void load()}
                      aria-label="重新加载任务模型"
                    >
                      <RefreshCw />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>重新加载</TooltipContent>
                </Tooltip>
              </div>
            ) : (
              <div className="divide-y rounded-md border bg-background">
                {profiles.map((profile) => (
                  <ProfileRow
                    key={profile.id}
                    profile={profile}
                    providers={providers}
                    onSaved={updateSavedProfile}
                  />
                ))}
              </div>
            )}
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}
