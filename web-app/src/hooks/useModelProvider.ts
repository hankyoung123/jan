import { localStorageKey } from '@/constants/localStorage'
import { backendStorage } from '@/lib/backendStorage'
import { API_KEY_FALLBACKS_SETTING_KEY } from '@/lib/provider-api-keys'
import { isLocalProvider } from '@/lib/utils'
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

const API_KEY_SETTING_KEY = 'api-key'

export function stripProviderSecrets(provider: ModelProvider): ModelProvider {
  const settings = provider.settings?.map((setting) =>
    setting.key === API_KEY_SETTING_KEY ||
    setting.key === API_KEY_FALLBACKS_SETTING_KEY
      ? {
          ...setting,
          controller_props: { ...setting.controller_props, value: '' },
        }
      : setting
  )
  return {
    ...provider,
    api_key: undefined,
    api_key_fallbacks: undefined,
    settings,
  }
}

type ModelProviderState = {
  providers: ModelProvider[]
  selectedProvider: string
  selectedModel: Model | null
  deletedModels: string[]
  getModelBy: (modelId: string) => Model | undefined
  setProviders: (providers: ModelProvider[]) => void
  getProviderByName: (providerName: string) => ModelProvider | undefined
  updateProvider: (providerName: string, data: Partial<ModelProvider>) => void
  selectModelProvider: (
    providerName: string,
    modelName: string
  ) => Model | undefined
  addProvider: (provider: ModelProvider) => void
  deleteProvider: (providerName: string) => void
  deleteModel: (modelId: string) => void
  addDeletedModels: (modelIds: string[]) => void
}

const remoteOnly = (providers: ModelProvider[]) =>
  providers.filter((provider) => !isLocalProvider(provider.provider))

export const useModelProvider = create<ModelProviderState>()(
  persist(
    (set, get) => ({
      providers: [],
      selectedProvider: '',
      selectedModel: null,
      deletedModels: [],
      getModelBy: (modelId) =>
        get()
          .providers.find(
            (provider) => provider.provider === get().selectedProvider
          )
          ?.models.find((model) => model.id === modelId),
      setProviders: (incoming) =>
        set((state) => {
          const providers: ModelProvider[] = remoteOnly(incoming).map((provider) => {
            const existing = state.providers.find(
              (item) => item.provider === provider.provider
            )
            const settings = provider.settings.map((setting) => {
              const saved = existing?.settings.find(
                (item) => item.key === setting.key
              )?.controller_props.value
              return saved === undefined
                ? setting
                : {
                    ...setting,
                    controller_props: {
                      ...setting.controller_props,
                      value: saved,
                    },
                  }
            })
            return {
              ...provider,
              active: existing?.active ?? provider.active ?? true,
              api_key: existing?.api_key || provider.api_key,
              api_key_fallbacks:
                existing?.api_key_fallbacks ?? provider.api_key_fallbacks,
              base_url: existing?.base_url || provider.base_url,
              models: existing?.models.length ? existing.models : provider.models,
              settings,
            }
          })
          for (const existing of remoteOnly(state.providers)) {
            if (
              !providers.some(
                (provider) => provider.provider === existing.provider
              )
            ) {
              providers.push(existing)
            }
          }
          const selectedStillExists = providers.some(
            (provider) => provider.provider === state.selectedProvider
          )
          return {
            providers,
            selectedProvider: selectedStillExists
              ? state.selectedProvider
              : (providers.find((provider) => provider.active)?.provider ?? ''),
            selectedModel: selectedStillExists ? state.selectedModel : null,
          }
        }),
      getProviderByName: (providerName) =>
        get().providers.find((provider) => provider.provider === providerName),
      updateProvider: (providerName, data) =>
        set((state) => ({
          providers: state.providers.map((provider) =>
            provider.provider === providerName
              ? { ...provider, ...data }
              : provider
          ),
          selectedModel:
            state.selectedProvider === providerName && Array.isArray(data.models)
              ? (data.models.find(
                  (model) => model.id === state.selectedModel?.id
                ) ?? null)
              : state.selectedModel,
        })),
      selectModelProvider: (providerName, modelName) => {
        const provider = get().providers.find(
          (item) => item.provider === providerName
        )
        const model = provider?.models.find((item) => item.id === modelName)
        set({ selectedProvider: providerName, selectedModel: model ?? null })
        return model
      },
      addProvider: (provider) => {
        if (isLocalProvider(provider.provider)) return
        set((state) => ({ providers: [...state.providers, provider] }))
      },
      deleteProvider: (providerName) =>
        set((state) => ({
          providers: state.providers.filter(
            (provider) => provider.provider !== providerName
          ),
          selectedProvider:
            state.selectedProvider === providerName ? '' : state.selectedProvider,
          selectedModel:
            state.selectedProvider === providerName ? null : state.selectedModel,
        })),
      deleteModel: (modelId) =>
        set((state) => ({
          providers: state.providers.map((provider) => ({
            ...provider,
            models: provider.models.filter((model) => model.id !== modelId),
          })),
          deletedModels: [...new Set([...state.deletedModels, modelId])],
        })),
      addDeletedModels: (modelIds) =>
        set((state) => ({
          deletedModels: [...new Set([...state.deletedModels, ...modelIds])],
        })),
    }),
    {
      name: localStorageKey.modelProvider,
      storage: createJSONStorage(() => backendStorage),
      skipHydration: true,
      partialize: (state) => ({
        ...state,
        providers: state.providers.map(stripProviderSecrets),
      }),
      migrate: (persistedState) => {
        const state = persistedState as ModelProviderState
        state.providers = remoteOnly(state.providers ?? [])
        if (isLocalProvider(state.selectedProvider)) {
          state.selectedProvider = ''
          state.selectedModel = null
        }
        return state
      },
      version: 18,
    }
  )
)
