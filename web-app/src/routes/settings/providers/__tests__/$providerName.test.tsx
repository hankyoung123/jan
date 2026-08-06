import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  fetchModelsFromProvider: vi.fn().mockResolvedValue(['deepseek-chat']),
  updateProvider: vi.fn(),
  updateSettings: vi.fn().mockResolvedValue(undefined),
}))

vi.mock('@tanstack/react-router', () => ({
  createFileRoute:
    () =>
    (config: Record<string, unknown>) => ({
      ...config,
      useParams: () => ({ providerName: 'deepseek' }),
    }),
  useNavigate: () => vi.fn(),
}))
vi.mock('@/containers/HeaderPage', () => ({
  default: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
}))
vi.mock('@/containers/SettingsMenu', () => ({
  default: () => <nav />,
}))
vi.mock('@/hooks/useModelProvider', () => ({
  useModelProvider: () => ({
    deleteProvider: vi.fn(),
    getProviderByName: () => ({
      provider: 'deepseek',
      active: true,
      models: [{ id: 'deepseek-chat' }],
      settings: [
        {
          key: 'api-key',
          title: 'API Key',
          description: 'Provider credential',
          controller_type: 'input',
          controller_props: { value: '', placeholder: 'Insert API Key' },
        },
      ],
      base_url: 'https://api.deepseek.com/v1',
      persist: true,
    }),
    updateProvider: mocks.updateProvider,
  }),
}))
vi.mock('@/hooks/useServiceHub', () => ({
  useServiceHub: () => ({
    providers: () => ({
      deleteProviderKeys: vi.fn(),
      fetchModelsFromProvider: mocks.fetchModelsFromProvider,
      updateSettings: mocks.updateSettings,
    }),
  }),
}))
vi.mock('@/lib/utils', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/utils')>()),
  getProviderTitle: (provider: string) => provider,
}))
vi.mock('@/constants/routes', () => ({
  route: { settings: { model_providers: '/settings/providers' } },
}))
vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}))

import { Route } from '../$providerName'

describe('Provider settings title actions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('keeps Save and Refresh clickable above the window drag area', async () => {
    const Component = Route.component as React.ComponentType
    render(<Component />)

    fireEvent.change(screen.getByPlaceholderText('Insert API Key'), {
      target: { value: 'test-key' },
    })

    const refresh = screen.getByRole('button', { name: /刷新模型/ })
    const save = screen.getByRole('button', { name: /保存/ })
    expect(refresh.parentElement).toHaveClass('relative', 'z-30')
    expect(refresh).toBeEnabled()
    expect(save).toBeEnabled()

    fireEvent.click(save)
    await waitFor(() => expect(mocks.updateSettings).toHaveBeenCalledOnce())

    fireEvent.click(refresh)
    await waitFor(() =>
      expect(mocks.fetchModelsFromProvider).toHaveBeenCalledOnce()
    )
  })
})
