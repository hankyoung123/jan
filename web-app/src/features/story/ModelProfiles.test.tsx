import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const h = vi.hoisted(() => ({
  engineRequest: vi.fn(),
  providers: [
    {
      active: true,
      provider: 'deepseek',
      base_url: 'https://api.deepseek.com/v1',
      settings: [],
      models: [{ id: 'deepseek-v4-flash' }, { id: 'deepseek-v4-pro' }],
    },
    {
      active: true,
      provider: 'openai',
      base_url: 'https://api.openai.com/v1',
      settings: [],
      models: [{ id: 'gpt-5-mini' }, { id: 'gpt-5.1' }],
    },
    {
      active: true,
      provider: 'llamacpp',
      base_url: 'http://127.0.0.1:39280/v1',
      settings: [],
      models: [{ id: 'qwen3-8b' }, { id: 'bge-m3' }],
    },
  ],
}))

vi.stubGlobal(
  'ResizeObserver',
  class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
)

vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, to, ...props }: { children: ReactNode; to: string }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))

vi.mock('@/hooks/useModelProvider', () => ({
  useModelProvider: (
    selector: (state: { providers: typeof h.providers }) => unknown
  ) => selector({ providers: h.providers }),
}))

vi.mock('./engine', () => ({
  engineRequest: h.engineRequest,
}))

import { ModelProfiles } from './ModelProfiles'

const configuredProviders = h.providers.map((provider) => ({
  ...provider,
  models: provider.models.map((model) => ({ ...model })),
}))

const profiles = [
  {
    id: 'character',
    name: 'Character',
    task_type: 'character',
    provider_id: 'llamacpp',
    model: 'qwen3-8b',
    max_output_tokens: 2048,
    timeout_seconds: 60,
    temperature: 0.7,
    enabled: true,
  },
  {
    id: 'resolver',
    name: 'Resolver',
    task_type: 'resolver',
    provider_id: 'openai',
    model: 'gpt-5-mini',
    max_output_tokens: 2048,
    timeout_seconds: 60,
    temperature: 0.2,
    enabled: true,
  },
  {
    id: 'editor',
    name: 'Editor',
    task_type: 'editor',
    provider_id: 'openai',
    model: 'gpt-5-mini',
    max_output_tokens: 2048,
    timeout_seconds: 60,
    temperature: 0.1,
    enabled: true,
  },
  {
    id: 'writer',
    name: 'Writer',
    task_type: 'writer',
    provider_id: 'openai',
    model: 'gpt-5-mini',
    max_output_tokens: 2048,
    timeout_seconds: 60,
    temperature: 0.8,
    enabled: true,
  },
  {
    id: 'embedding',
    name: 'Embedding',
    task_type: 'embedding',
    provider_id: 'llamacpp',
    model: 'bge-m3',
    max_output_tokens: 2048,
    timeout_seconds: 60,
    temperature: null,
    enabled: true,
  },
] as const

function successfulRequests(path: string, init?: RequestInit) {
  if (path === '/models/profiles') return Promise.resolve(profiles)
  if (path === '/models/usage') {
    return Promise.resolve({
      requests: 12,
      prompt_tokens: 200,
      completion_tokens: 145,
      total_tokens: 345,
    })
  }
  if (path === '/models/profiles/writer' && init?.method === 'PUT') {
    return Promise.resolve(JSON.parse(String(init.body)))
  }
  throw new Error(`Unexpected request: ${path}`)
}

describe('ModelProfiles', () => {
  beforeEach(() => {
    h.providers = configuredProviders.map((provider) => ({
      ...provider,
      models: provider.models.map((model) => ({ ...model })),
    }))
    h.engineRequest.mockReset()
    h.engineRequest.mockImplementation(successfulRequests)
  })

  it('loads all five task routes and usage inside the Jan model center', async () => {
    render(<ModelProfiles />)

    expect(await screen.findByText('角色')).toBeInTheDocument()
    expect(screen.getByText('裁决')).toBeInTheDocument()
    expect(screen.getByText('审核')).toBeInTheDocument()
    expect(screen.getByText('写作')).toBeInTheDocument()
    expect(screen.getByText('嵌入')).toBeInTheDocument()
    expect(screen.getByText('12 次调用 · 345 Token')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '打开 Provider 设置' })).toHaveAttribute(
      'href',
      '/settings/providers'
    )
    expect(screen.queryByText(/api key/i)).not.toBeInTheDocument()
  })

  it('saves model and advanced limits without adding Provider secrets', async () => {
    render(<ModelProfiles />)
    await screen.findByText('写作')

    fireEvent.change(screen.getAllByLabelText('模型')[3], {
      target: { value: 'gpt-5.1' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: '写作高级设置' })
    )
    fireEvent.change(screen.getByLabelText('最大输出 Token'), {
      target: { value: '4096' },
    })
    fireEvent.click(screen.getByLabelText('启用写作'))
    fireEvent.click(screen.getByRole('button', { name: '保存写作配置' }))

    await waitFor(() => {
      expect(h.engineRequest).toHaveBeenCalledWith(
        '/models/profiles/writer',
        expect.objectContaining({ method: 'PUT' })
      )
    })
    const request = h.engineRequest.mock.calls.find(
      ([path]) => path === '/models/profiles/writer'
    )
    const body = JSON.parse(String(request?.[1]?.body))
    expect(body).toMatchObject({
      id: 'writer',
      provider_id: 'openai',
      model: 'gpt-5.1',
      max_output_tokens: 4096,
      enabled: false,
    })
    expect(JSON.stringify(body).toLowerCase()).not.toContain('api_key')
  })

  it('shows thinking strength in advanced settings and disables it off DeepSeek', async () => {
    render(<ModelProfiles />)
    await screen.findByText('写作')

    fireEvent.click(screen.getByRole('button', { name: '写作高级设置' }))

    const select = screen.getByRole('button', { name: '写作思考强度' })
    expect(select).toBeDisabled()
    expect(select).toHaveTextContent('关闭')
    expect(screen.getByText('仅 DeepSeek 生效')).toBeInTheDocument()
  })

  it('saves a chosen reasoning effort for a DeepSeek task', async () => {
    const user = userEvent.setup()
    render(<ModelProfiles />)
    await screen.findByText('写作')

    await user.click(screen.getByRole('button', { name: '写作 Provider' }))
    await user.click(await screen.findByRole('menuitemradio', { name: /DeepSeek/ }))
    await user.click(screen.getByRole('button', { name: '写作高级设置' }))

    const select = screen.getByRole('button', { name: '写作思考强度' })
    expect(select).toBeEnabled()
    expect(screen.getByText('V4 Pro 当前将低档映射为高档')).toBeInTheDocument()
    await user.click(select)
    await user.click(await screen.findByRole('menuitemradio', { name: '低' }))
    await user.click(screen.getByRole('button', { name: '保存写作配置' }))

    await waitFor(() => {
      expect(h.engineRequest).toHaveBeenCalledWith(
        '/models/profiles/writer',
        expect.objectContaining({ method: 'PUT' })
      )
    })
    const request = h.engineRequest.mock.calls.find(
      ([path]) => path === '/models/profiles/writer'
    )
    const body = JSON.parse(String(request?.[1]?.body))
    expect(body.reasoning_effort).toBe('low')
  })

  it('offers an in-place retry when the Story Engine is unavailable', async () => {
    h.engineRequest.mockRejectedValue(new Error('Story Engine is not running'))
    render(<ModelProfiles />)

    expect(
      await screen.findByText('Story Engine is not running')
    ).toBeInTheDocument()

    h.engineRequest.mockImplementation(successfulRequests)
    fireEvent.click(
      screen.getByRole('button', { name: '重新加载任务模型' })
    )

    expect(await screen.findByText('写作')).toBeInTheDocument()
  })

  it('keeps profiles available when aggregate usage cannot be loaded', async () => {
    h.engineRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === '/models/usage') return Promise.reject(new Error('no usage'))
      return successfulRequests(path, init)
    })
    render(<ModelProfiles />)

    expect(await screen.findByText('写作')).toBeInTheDocument()
    expect(screen.getByText('0 次调用 · 0 Token')).toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('keeps explicit model IDs editable while Jan Provider state hydrates', async () => {
    h.providers = []
    render(<ModelProfiles />)
    await screen.findByText('写作')

    const modelInputs = screen.getAllByLabelText('模型')
    expect(modelInputs).toHaveLength(5)
    expect(modelInputs.every((input) => !input.hasAttribute('disabled'))).toBe(
      true
    )

    fireEvent.change(modelInputs[3], { target: { value: 'gpt-5.1' } })
    expect(
      screen.getByRole('button', { name: '保存写作配置' })
    ).toBeEnabled()
  })
})
