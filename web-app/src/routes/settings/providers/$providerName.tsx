import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { RefreshCw, Save, Trash2 } from 'lucide-react'
import { useMemo, useState } from 'react'
import { toast } from 'sonner'

import HeaderPage from '@/containers/HeaderPage'
import SettingsMenu from '@/containers/SettingsMenu'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { route } from '@/constants/routes'
import { useModelProvider } from '@/hooks/useModelProvider'
import { useServiceHub } from '@/hooks/useServiceHub'
import { getProviderTitle } from '@/lib/utils'

export const Route = createFileRoute('/settings/providers/$providerName')({
  component: ProviderSettings,
})

function settingValue(provider: ModelProvider, key: string): string {
  const value = provider.settings.find((setting) => setting.key === key)
    ?.controller_props.value
  return value == null ? '' : String(value)
}

function ProviderSettings() {
  const { providerName } = Route.useParams()
  const navigate = useNavigate()
  const serviceHub = useServiceHub()
  const { getProviderByName, updateProvider, deleteProvider } =
    useModelProvider()
  const provider = getProviderByName(providerName)
  const [newModel, setNewModel] = useState('')
  const [working, setWorking] = useState(false)
  const modelIds = useMemo(
    () => provider?.models.map((model) => model.id) ?? [],
    [provider?.models]
  )

  if (!provider) {
    return (
      <div className="flex h-svh flex-col">
        <HeaderPage>Provider settings</HeaderPage>
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <SettingsMenu />
          <p className="p-6 text-sm text-muted-foreground">
            Provider “{providerName}” 不存在。
          </p>
        </div>
      </div>
    )
  }

  const updateSetting = (
    key: string,
    value: string | boolean | number
  ) => {
    const settings = provider.settings.map((setting) =>
      setting.key === key
        ? {
            ...setting,
            controller_props: { ...setting.controller_props, value },
          }
        : setting
    )
    updateProvider(provider.provider, {
      settings,
      ...(key === 'api-key' ? { api_key: String(value) } : {}),
      ...(key === 'base-url' ? { base_url: String(value) } : {}),
    })
  }

  const save = async () => {
    setWorking(true)
    try {
      await serviceHub
        .providers()
        .updateSettings(provider.provider, provider.settings)
      toast.success('Cloud Provider 设置已保存')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '保存失败')
    } finally {
      setWorking(false)
    }
  }

  const refreshModels = async () => {
    setWorking(true)
    try {
      const ids = await serviceHub.providers().fetchModelsFromProvider(provider)
      updateProvider(provider.provider, {
        models: ids.map((id) =>
          provider.models.find((model) => model.id === id) ?? { id }
        ),
      })
      toast.success(`已读取 ${ids.length} 个云端模型`)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '模型列表读取失败')
    } finally {
      setWorking(false)
    }
  }

  return (
    <div className="flex h-svh flex-col">
      <HeaderPage>
        <div className="flex w-full items-center justify-between pr-4">
          <span className="font-studio text-base font-medium">
            {getProviderTitle(provider.provider)}
          </span>
          <div className="relative z-30 flex gap-2">
            <Button
              disabled={working}
              onClick={() => void refreshModels()}
              size="sm"
              variant="outline"
            >
              <RefreshCw size={14} /> 刷新模型
            </Button>
            <Button disabled={working} onClick={() => void save()} size="sm">
              <Save size={14} /> 保存
            </Button>
          </div>
        </div>
      </HeaderPage>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <SettingsMenu />
        <main className="w-full overflow-y-auto p-4 pt-0">
          <section className="border bg-background p-5">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="font-studio text-lg">Cloud Provider</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Story Engine 仅通过 Jan 的远程 Provider 桥调用这些模型。
                </p>
              </div>
              <Switch
                checked={provider.active}
                onCheckedChange={(active) =>
                  updateProvider(provider.provider, { active })
                }
              />
            </div>
            <div className="mt-6 grid gap-5 lg:grid-cols-2">
              {provider.settings.map((setting) => (
                <label className="block" key={setting.key}>
                  <span className="text-sm font-medium">{setting.title}</span>
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {setting.description}
                  </span>
                  {setting.controller_type === 'checkbox' ? (
                    <Switch
                      className="mt-3"
                      checked={Boolean(setting.controller_props.value)}
                      onCheckedChange={(value) =>
                        updateSetting(setting.key, value)
                      }
                    />
                  ) : (
                    <Input
                      className="mt-3"
                      onChange={(event) =>
                        updateSetting(setting.key, event.target.value)
                      }
                      placeholder={setting.controller_props.placeholder}
                      type={
                        setting.key === 'api-key' ||
                        setting.controller_props.type === 'password'
                          ? 'password'
                          : 'text'
                      }
                      value={settingValue(provider, setting.key)}
                    />
                  )}
                </label>
              ))}
            </div>
          </section>

          <section className="mt-4 border bg-background p-5">
            <h2 className="font-studio text-lg">远程模型</h2>
            <div className="mt-4 flex gap-2">
              <Input
                aria-label="模型 ID"
                onChange={(event) => setNewModel(event.target.value)}
                placeholder="例如 gpt-5.2"
                value={newModel}
              />
              <Button
                disabled={!newModel.trim()}
                onClick={() => {
                  const id = newModel.trim()
                  if (!modelIds.includes(id)) {
                    updateProvider(provider.provider, {
                      models: [...provider.models, { id }],
                    })
                  }
                  setNewModel('')
                }}
                variant="outline"
              >
                添加
              </Button>
            </div>
            <div className="mt-4 divide-y border-y">
              {provider.models.map((model) => (
                <div
                  className="flex items-center justify-between py-3 text-sm"
                  key={model.id}
                >
                  <span className="font-mono text-xs">{model.id}</span>
                  <Button
                    aria-label={`删除 ${model.id}`}
                    onClick={() =>
                      updateProvider(provider.provider, {
                        models: provider.models.filter(
                          (item) => item.id !== model.id
                        ),
                      })
                    }
                    size="icon-xs"
                    variant="ghost"
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              ))}
            </div>
          </section>

          {!provider.persist && (
            <Button
              className="mt-6"
              onClick={async () => {
                await serviceHub
                  .providers()
                  .deleteProviderKeys(provider.provider)
                deleteProvider(provider.provider)
                await navigate({ to: route.settings.model_providers })
              }}
              variant="destructive"
            >
              <Trash2 size={14} /> 删除 Provider
            </Button>
          )}
        </main>
      </div>
    </div>
  )
}
