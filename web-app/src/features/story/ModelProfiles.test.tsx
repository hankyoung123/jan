import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ModelProfiles } from './ModelProfiles'

const engineRequest = vi.fn()
const providers = [
  { provider: 'openai', active: true, models: [{ id: 'story-model' }] },
  { provider: 'anthropic', active: true, models: [{ id: 'story-model' }] },
  { provider: 'llamacpp', active: true, models: [{ id: 'local-model' }] },
]

vi.mock('./engine', () => ({
  engineRequest: (...args: unknown[]) => engineRequest(...args),
}))
vi.mock('@/hooks/useModelProvider', () => ({
  useModelProvider: (selector: (state: { providers: typeof providers }) => unknown) =>
    selector({ providers }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: unknown }) => <span>{children as never}</span>,
}))

const profiles = [
  {
    name: '角色演员',
    agent_type: 'actor',
    default_system_prompt: '只使用角色已知信息。',
    model: null,
    reasoning_effort: null,
    max_output_tokens: 2048,
    timeout_seconds: 60,
    temperature: 0.7,
  },
  {
    name: '正文作者',
    agent_type: 'writer',
    default_system_prompt: '写成小说正文。',
    model: 'openai/story-model',
    reasoning_effort: 'high',
    max_output_tokens: null,
    timeout_seconds: 300,
    temperature: 0.8,
  },
]

describe('ModelProfiles', () => {
  let failPatch = false

  beforeEach(() => {
    failPatch = false
    engineRequest.mockReset()
    engineRequest.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/agent-profiles') return Promise.resolve(profiles)
      if (path === '/models/usage') {
        return Promise.resolve({
          requests: 1,
          prompt_tokens: 2,
          completion_tokens: 3,
          total_tokens: 5,
        })
      }
      if (path === '/agent-profiles/actor') {
        if (failPatch) return Promise.reject(new Error('provider unavailable'))
        return Promise.resolve({
          ...profiles[0],
          ...JSON.parse(String(options?.body ?? '{}')),
        })
      }
      throw new Error(path)
    })
  })

  it('shows one settings surface without project or per-character assignments', async () => {
    render(<ModelProfiles />)

    expect(await screen.findByText('Agent 设置')).toBeInTheDocument()
    expect(screen.queryByText('项目 Agent 覆写')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /新增角色/ })).not.toBeInTheDocument()
  })

  it('immediately saves model and reasoning selections', async () => {
    render(<ModelProfiles />)
    const model = await screen.findByLabelText('角色演员模型')
    fireEvent.change(model, { target: { value: 'anthropic/story-model' } })

    await waitFor(() =>
      expect(engineRequest).toHaveBeenCalledWith(
        '/agent-profiles/actor',
        expect.objectContaining({ method: 'PATCH' })
      )
    )
    fireEvent.click(screen.getByRole('button', { name: '角色演员高级参数' }))
    fireEvent.change(screen.getByLabelText('角色演员思考强度'), {
      target: { value: 'medium' },
    })

    await waitFor(() => {
      const patches = engineRequest.mock.calls.filter(
        ([path, request]) =>
          path === '/agent-profiles/actor' && request?.method === 'PATCH'
      )
      expect(
        patches.some(([, request]) =>
          String(request.body).includes('reasoning_effort')
        )
      ).toBe(true)
    })
  })

  it('debounces editable prompts and numeric values', async () => {
    render(<ModelProfiles />)
    await screen.findByLabelText('角色演员模型')
    fireEvent.click(screen.getByRole('button', { name: '角色演员高级参数' }))
    fireEvent.change(screen.getByLabelText('默认系统提示词'), {
      target: { value: '保持克制。' },
    })
    fireEvent.change(document.getElementById('agent-actor-timeout')!, {
      target: { value: '180' },
    })

    await waitFor(
      () => {
        const patches = engineRequest.mock.calls.filter(
          ([path, request]) =>
            path === '/agent-profiles/actor' && request?.method === 'PATCH'
        )
        const last = JSON.parse(String(patches.at(-1)?.[1]?.body))
        expect(last).toMatchObject({
          default_system_prompt: '保持克制。',
          timeout_seconds: 180,
        })
      },
      { timeout: 2000 }
    )
  })

  it('supports provider-controlled output length with a blank token field', async () => {
    render(<ModelProfiles />)
    await screen.findByLabelText('正文作者模型')
    fireEvent.click(screen.getByRole('button', { name: '正文作者高级参数' }))

    expect(document.getElementById('agent-writer-tokens')).toHaveValue(null)
    expect(document.getElementById('agent-writer-timeout')).toHaveValue(300)
  })

  it('restores the confirmed value when autosave fails', async () => {
    failPatch = true
    render(<ModelProfiles />)
    const model = await screen.findByLabelText('角色演员模型')
    fireEvent.change(model, { target: { value: 'openai/story-model' } })

    expect(await screen.findByText('更新失败')).toBeInTheDocument()
    expect(model).toHaveValue('')
  })
})
