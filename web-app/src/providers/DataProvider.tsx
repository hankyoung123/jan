import { invoke, isTauri } from '@tauri-apps/api/core'
import { useEffect } from 'react'

import { useAppUpdater } from '@/hooks/useAppUpdater'
import { useAssistant } from '@/hooks/useAssistant'
import { useGeneralSetting } from '@/hooks/useGeneralSetting'
import { DEFAULT_MCP_SETTINGS, useMCPServers } from '@/hooks/useMCPServers'
import { useModelProvider } from '@/hooks/useModelProvider'
import { useServiceHub } from '@/hooks/useServiceHub'
import { useThreads } from '@/hooks/useThreads'
import { ExtensionManager } from '@/lib/extension'
import {
  providerHasRemoteApiKeys,
  providerRemoteApiKeyChain,
} from '@/lib/provider-api-keys'
import { isDev } from '@/lib/utils'

type RegisterProviderRequest = {
  provider: string
  api_key?: string
  api_keys?: string[]
  base_url?: string
  custom_headers: ProviderCustomHeader[]
  models: string[]
  api_type?: ProviderApiType
  model_api_types: Record<string, ProviderApiType>
}

async function registerRemoteProvider(provider: ModelProvider) {
  if (!isTauri()) return
  const keys = providerRemoteApiKeyChain(provider)
  if (!keys.length) return
  const request: RegisterProviderRequest = {
    provider: provider.provider,
    api_key: keys[0],
    api_keys: keys.slice(1),
    base_url: provider.base_url,
    custom_headers: provider.custom_header ?? [],
    models: provider.models.map((model) => model.id),
    api_type: provider.api_type,
    model_api_types: Object.fromEntries(
      provider.models.flatMap((model) =>
        model.api_type ? [[model.id, model.api_type]] : []
      )
    ),
  }
  await invoke('register_provider_config', { request })
}

async function applyKeyringKeys() {
  if (!isTauri()) return
  const store = useModelProvider.getState()
  await Promise.all(
    store.providers.map(async (provider) => {
      const keys = await invoke<string[]>('get_provider_keys', {
        provider: provider.provider,
      }).catch(() => [])
      if (keys.length) {
        store.updateProvider(provider.provider, {
          api_key: keys[0],
          api_key_fallbacks: keys.slice(1),
        })
      }
    })
  )
}

let registeredProviderNames = new Set<string>()

async function syncRemoteProviders() {
  if (!isTauri()) return
  const active = useModelProvider
    .getState()
    .providers.filter(
      (provider) => provider.active && providerHasRemoteApiKeys(provider)
    )
  await Promise.all(
    active.map((provider) =>
      registerRemoteProvider(provider).catch((error) =>
        console.error(`Failed to register ${provider.provider}:`, error)
      )
    )
  )
  const current = new Set(active.map((provider) => provider.provider))
  await Promise.all(
    [...registeredProviderNames]
      .filter((provider) => !current.has(provider))
      .map((provider) =>
        invoke('unregister_provider_config', { provider }).catch(() => {})
      )
  )
  registeredProviderNames = current
}

export function DataProvider() {
  const serviceHub = useServiceHub()
  const setProviders = useModelProvider((state) => state.setProviders)
  const providers = useModelProvider((state) => state.providers)
  const { checkForUpdate } = useAppUpdater()
  const autoUpdateCheck = useGeneralSetting((state) => state.autoUpdateCheck)
  const { setServers, setSettings } = useMCPServers()
  const { setAssistants } = useAssistant()
  const { setThreads } = useThreads()
  const setThreadsLoading = useThreads((state) => state.setThreadsLoading)

  useEffect(() => {
    void serviceHub
      .providers()
      .getProviders()
      .then(async (fetched) => {
        setProviders(fetched)
        await applyKeyringKeys()
        await syncRemoteProviders()
      })
    void serviceHub
      .mcp()
      .getMCPConfig()
      .then((data) => {
        setServers(data.mcpServers ?? {})
        setSettings(data.mcpSettings ?? DEFAULT_MCP_SETTINGS)
      })
    void serviceHub
      .assistants()
      .getAssistants()
      .then((items) =>
        setAssistants(
          items && items.length ? (items as unknown as Assistant[]) : null
        )
      )
      .catch(() => setAssistants(null))
  }, [serviceHub, setAssistants, setProviders, setServers, setSettings])

  useEffect(() => {
    let cancelled = false
    let attempt = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = () => {
      void serviceHub
        .threads()
        .fetchThreads()
        .then((threads) => {
          if (cancelled) return
          setThreads(threads)
          setThreadsLoading(false)
        })
        .catch(() => {
          if (cancelled || attempt >= 20) {
            setThreadsLoading(false)
            return
          }
          attempt += 1
          timer = setTimeout(load, Math.min(1000, 150 * attempt))
        })
    }
    const unsubscribe = ExtensionManager.getInstance().onRegistrationChange(
      () => {
        attempt = 0
        clearTimeout(timer)
        load()
      }
    )
    load()
    return () => {
      cancelled = true
      clearTimeout(timer)
      unsubscribe()
    }
  }, [serviceHub, setThreads, setThreadsLoading])

  useEffect(() => {
    void syncRemoteProviders()
  }, [providers])

  useEffect(() => {
    if (isDev() || !autoUpdateCheck) return
    const interval = window.setInterval(
      () => void checkForUpdate(),
      Number(UPDATE_CHECK_INTERVAL_MS)
    )
    void checkForUpdate()
    return () => window.clearInterval(interval)
  }, [autoUpdateCheck, checkForUpdate])

  return null
}
