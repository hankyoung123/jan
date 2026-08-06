import { ChevronsUpDown, Settings } from 'lucide-react'
import { memo, useEffect, useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import { route } from '@/constants/routes'
import { useModelProvider } from '@/hooks/useModelProvider'
import { useThreads } from '@/hooks/useThreads'
import { cn, getModelDisplayName, getProviderTitle } from '@/lib/utils'
import ProvidersAvatar from './ProvidersAvatar'

type DropdownModelProviderProps = {
  model?: ThreadModel
  useLastUsedModel?: boolean
}

const LAST_CLOUD_MODEL_KEY = 'story-engine:last-cloud-model'

export const DropdownModelProvider = memo(function DropdownModelProvider({
  model,
  useLastUsedModel = false,
}: DropdownModelProviderProps) {
  const {
    providers,
    selectedProvider,
    selectedModel,
    selectModelProvider,
    getProviderByName,
  } = useModelProvider()
  const { updateCurrentThreadModel } = useThreads()
  const navigate = useNavigate()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const choices = useMemo(
    () =>
      providers
        .filter((provider) => provider.active)
        .flatMap((provider) =>
          provider.models.map((item) => ({ provider, model: item }))
        )
        .filter(({ provider, model: item }) =>
          `${provider.provider} ${item.id} ${item.displayName ?? ''}`
            .toLowerCase()
            .includes(search.toLowerCase())
        ),
    [providers, search]
  )

  useEffect(() => {
    if (model) {
      selectModelProvider(model.provider, model.id)
      return
    }
    if (!useLastUsedModel || selectedModel) return
    const saved = localStorage.getItem(LAST_CLOUD_MODEL_KEY)
    if (!saved) return
    try {
      const value = JSON.parse(saved) as ThreadModel
      selectModelProvider(value.provider, value.id)
    } catch {
      localStorage.removeItem(LAST_CLOUD_MODEL_KEY)
    }
  }, [model, selectModelProvider, selectedModel, useLastUsedModel])

  if (!providers.length) return null
  const provider = getProviderByName(selectedProvider)
  const label = selectedModel
    ? getModelDisplayName(selectedModel)
    : '选择云端模型'

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          className="relative z-20 flex min-w-0 items-center gap-2 rounded-full border px-4 py-1.5 text-sm font-medium"
          type="button"
        >
          {provider && <ProvidersAvatar provider={provider} />}
          <span
            className={cn(
              'max-w-56 truncate',
              !selectedModel && 'text-muted-foreground'
            )}
          >
            {label}
          </span>
          <ChevronsUpDown className="size-4 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-80 p-0">
        <div className="border-b p-2">
          <input
            autoFocus
            className="h-9 w-full bg-transparent px-2 text-sm outline-none"
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索 Cloud Provider / Model"
            value={search}
          />
        </div>
        <div className="max-h-72 overflow-y-auto p-1">
          {choices.map(({ provider: itemProvider, model: item }) => (
            <button
              className="flex w-full items-center gap-3 rounded-sm px-3 py-2 text-left hover:bg-accent"
              key={`${itemProvider.provider}/${item.id}`}
              onClick={() => {
                selectModelProvider(itemProvider.provider, item.id)
                updateCurrentThreadModel({
                  provider: itemProvider.provider,
                  id: item.id,
                })
                localStorage.setItem(
                  LAST_CLOUD_MODEL_KEY,
                  JSON.stringify({ provider: itemProvider.provider, id: item.id })
                )
                setOpen(false)
              }}
              type="button"
            >
              <ProvidersAvatar provider={itemProvider} />
              <span className="min-w-0">
                <strong className="block truncate text-sm">
                  {getModelDisplayName(item)}
                </strong>
                <span className="block truncate text-xs font-normal text-muted-foreground">
                  {getProviderTitle(itemProvider.provider)}
                </span>
              </span>
            </button>
          ))}
          {!choices.length && (
            <p className="p-4 text-center text-sm text-muted-foreground">
              没有匹配的云端模型
            </p>
          )}
        </div>
        {provider && (
          <button
            className="flex w-full items-center gap-2 border-t px-4 py-3 text-sm text-muted-foreground hover:bg-accent"
            onClick={() => {
              setOpen(false)
              void navigate({
                to: route.settings.providers,
                params: { providerName: provider.provider },
              })
            }}
            type="button"
          >
            <Settings size={14} /> Provider 设置
          </button>
        )}
      </PopoverContent>
    </Popover>
  )
})

export default DropdownModelProvider
