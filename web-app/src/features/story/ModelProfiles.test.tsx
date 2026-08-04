import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ModelProfiles } from './ModelProfiles'

const engineRequest = vi.fn()
const providers = [
  {
    provider: 'openai',
    active: true,
    models: [{ id: 'shared-model' }],
  },
  {
    provider: 'anthropic',
    active: true,
    models: [{ id: 'shared-model' }],
  },
  {
    provider: 'llamacpp',
    active: true,
    models: [{ id: 'local-model' }],
  },
]

vi.mock('./engine', () => ({
  engineRequest: (...args: unknown[]) => engineRequest(...args),
}))
vi.mock('@/hooks/useModelProvider', () => ({
  useModelProvider: (selector: (state: { providers: typeof providers }) => unknown) =>
    selector({ providers }),
}))
vi.mock('./activeProject', () => ({
  useActiveStoryProjectId: () => 'fog-harbor',
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: unknown }) => <span>{children as never}</span>,
}))

const profiles = [
  {
    id: 'actor',
    task_type: 'actor',
    model_ref: null,
    max_output_tokens: 2048,
    timeout_seconds: 60,
    temperature: 0.7,
  },
  {
    id: 'actor-precise',
    task_type: 'actor',
    model_ref: 'anthropic/shared-model',
    max_output_tokens: 2048,
    timeout_seconds: 60,
    temperature: 0.2,
  },
  {
    id: 'writer',
    task_type: 'writer',
    model_ref: 'openai/shared-model',
    max_output_tokens: 4096,
    timeout_seconds: 60,
    temperature: 0.7,
  },
]
const policy = {
  schema_version: 1,
  task_profile_ids: {
    actor: 'actor',
    game_master: 'game-master',
    wiki_maintenance: 'wiki-maintenance',
    editor: 'editor',
    writer: 'writer',
  },
  agent_profile_ids: {},
}
const characters = [
  {
    id: 'chen-mo',
    display_name: '陈默',
    type: 'active',
    identity: '调查员',
    core_desire: '寻找真相',
    current_goal: '检查灯塔',
    known_fact_ids: [],
    relationships: [],
    resources: [],
    version: 0,
  },
]

describe('ModelProfiles', () => {
  let failPatch = false

  beforeEach(() => {
    failPatch = false
    engineRequest.mockReset()
    engineRequest.mockImplementation((path: string, options?: RequestInit) => {
      if (path === '/models/profiles') return Promise.resolve(profiles)
      if (path === '/models/usage') {
        return Promise.resolve({
          requests: 1,
          prompt_tokens: 2,
          completion_tokens: 3,
          total_tokens: 5,
        })
      }
      if (path === '/projects/fog-harbor/model-policy') {
        if (options?.method === 'PATCH') {
          const patch = JSON.parse(String(options.body)) as {
            agent_profile_ids?: Record<string, string | null>
          }
          const agentProfileIds = { ...policy.agent_profile_ids }
          for (const [agentId, profileId] of Object.entries(
            patch.agent_profile_ids ?? {}
          )) {
            if (profileId === null) delete agentProfileIds[agentId]
            else agentProfileIds[agentId] = profileId
          }
          return Promise.resolve({
            ...policy,
            agent_profile_ids: agentProfileIds,
          })
        }
        if (options?.method === 'PUT') {
          return Promise.resolve(JSON.parse(String(options.body)))
        }
        return Promise.resolve(policy)
      }
      if (path === '/projects/fog-harbor/characters') {
        return Promise.resolve(characters)
      }
      if (path === '/models/profiles/actor') {
        if (failPatch) return Promise.reject(new Error('provider unavailable'))
        const patch = JSON.parse(String(options?.body ?? '{}'))
        return Promise.resolve({ ...profiles[0], ...patch })
      }
      throw new Error(path)
    })
  })

  it('requires an explicit provider-qualified model and retains duplicate model IDs', async () => {
    render(<ModelProfiles />)
    const select = await screen.findByLabelText('角色模型')

    expect(select).toHaveValue('')
    const options = Array.from((select as HTMLSelectElement).options)
    expect(options.find((option) => option.text === 'OpenAI · shared-model')?.value).toBe(
      'openai/shared-model'
    )
    expect(
      options.find((option) => option.text === 'Anthropic · shared-model')?.value
    ).toBe('anthropic/shared-model')
    expect(screen.queryByRole('option', { name: /local-model/ })).not.toBeInTheDocument()
    expect(options.find((option) => option.text === '未选择模型')).toBeDefined()
  })

  it('loads model profiles when current project data is incompatible', async () => {
    engineRequest.mockImplementation((path: string) => {
      if (path === '/models/profiles') return Promise.resolve(profiles)
      if (path === '/models/usage') {
        return Promise.resolve({
          requests: 0,
          prompt_tokens: 0,
          completion_tokens: 0,
          total_tokens: 0,
        })
      }
      if (
        path === '/projects/fog-harbor/model-policy' ||
        path === '/projects/fog-harbor/characters'
      ) {
        return Promise.reject(new Error('Project state is incompatible'))
      }
      throw new Error(path)
    })

    render(<ModelProfiles />)

    expect(await screen.findByLabelText('角色模型')).toBeInTheDocument()
    expect(
      screen.queryByText('Project state is incompatible')
    ).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '重新加载' })).not.toBeInTheDocument()
  })

  it('autosaves the provider-qualified model reference', async () => {
    render(<ModelProfiles />)
    fireEvent.change(await screen.findByLabelText('角色模型'), {
      target: { value: 'openai/shared-model' },
    })

    await waitFor(() =>
      expect(engineRequest).toHaveBeenCalledWith(
        '/models/profiles/actor',
        expect.objectContaining({ method: 'PATCH' })
      )
    )
    const options = engineRequest.mock.calls.find(
      ([path, request]) =>
        path === '/models/profiles/actor' && request?.method === 'PATCH'
    )?.[1]
    expect(JSON.parse(options.body)).toEqual({ model_ref: 'openai/shared-model' })
    expect(await screen.findByText('已更新')).toBeInTheDocument()
  })

  it('autosaves the selected thinking intensity', async () => {
    render(<ModelProfiles />)
    fireEvent.click(await screen.findByRole('button', { name: '角色模型高级参数' }))
    fireEvent.change(await screen.findByLabelText('角色模型思考强度'), {
      target: { value: 'medium' },
    })

    await waitFor(() =>
      expect(engineRequest).toHaveBeenCalledWith(
        '/models/profiles/actor',
        expect.objectContaining({ method: 'PATCH' })
      )
    )
    const options = engineRequest.mock.calls.find(
      ([path, request]) =>
        path === '/models/profiles/actor' && request?.method === 'PATCH'
    )?.[1]
    expect(JSON.parse(options.body).reasoning_effort).toBe('medium')
  })

  it('debounces numeric profile changes and sends a partial patch', async () => {
    render(<ModelProfiles />)
    fireEvent.click(await screen.findByRole('button', { name: '角色模型高级参数' }))
    fireEvent.change(await screen.findByLabelText('最大输出 Token'), {
      target: { value: '3072' },
    })

    await waitFor(
      () =>
        expect(engineRequest).toHaveBeenCalledWith(
          '/models/profiles/actor',
          expect.objectContaining({ method: 'PATCH' })
        ),
      { timeout: 2000 }
    )
    const options = engineRequest.mock.calls.find(
      ([path, request]) =>
        path === '/models/profiles/actor' && request?.method === 'PATCH'
    )?.[1]
    expect(JSON.parse(options.body)).toEqual({ max_output_tokens: 3072 })
  })

  it('does not expose a token ceiling for the writer profile', async () => {
    render(<ModelProfiles />)

    fireEvent.click(await screen.findByRole('button', { name: '写作模型高级参数' }))

    expect(document.getElementById('writer-tokens')).not.toBeInTheDocument()
    expect(document.getElementById('writer-timeout')).toBeInTheDocument()
  })

  it('exposes bridge-compatible thinking intensity options', async () => {
    render(<ModelProfiles />)
    fireEvent.click(await screen.findByRole('button', { name: '角色模型高级参数' }))
    const select = (await screen.findByLabelText(
      '角色模型思考强度'
    )) as HTMLSelectElement
    const options = Object.fromEntries(
      Array.from(select.options).map((option) => [option.text, option.value])
    )

    expect(options['跟随模型默认']).toBe('')
    expect(options['关闭思考']).toBe('none')
    expect(options['最小']).toBe('minimal')
    expect(options['最大']).toBe('max')
  })

  it('autosaves a per-character actor profile override', async () => {
    render(<ModelProfiles />)
    fireEvent.change(await screen.findByLabelText('陈默 Agent Profile'), {
      target: { value: 'actor-precise' },
    })

    await waitFor(() =>
      expect(engineRequest).toHaveBeenCalledWith(
        '/projects/fog-harbor/model-policy',
        expect.objectContaining({ method: 'PATCH' })
      )
    )
    const options = engineRequest.mock.calls.find(
      ([path, request]) =>
        path === '/projects/fog-harbor/model-policy' && request?.method === 'PATCH'
    )?.[1]
    expect(JSON.parse(options.body).agent_profile_ids).toEqual({
      'chen-mo': 'actor-precise',
    })
  })

  it('restores the confirmed value when autosave fails', async () => {
    failPatch = true
    render(<ModelProfiles />)
    const select = await screen.findByLabelText('角色模型')
    fireEvent.change(select, {
      target: { value: 'openai/shared-model' },
    })

    expect(await screen.findByText('更新失败')).toBeInTheDocument()
    expect(select).toHaveValue('')
  })

  it('coalesces rapid consecutive changes into the final value', async () => {
    render(<ModelProfiles />)
    const select = await screen.findByLabelText('角色模型')
    fireEvent.change(select, { target: { value: 'openai/shared-model' } })
    fireEvent.change(select, { target: { value: 'anthropic/shared-model' } })

    await waitFor(() => {
      const patches = engineRequest.mock.calls.filter(
        ([path, request]) =>
          path === '/models/profiles/actor' && request?.method === 'PATCH'
      )
      expect(patches.length).toBeGreaterThan(0)
      const last = JSON.parse((patches.at(-1)?.[1] as RequestInit).body as string)
      expect(last.model_ref).toBe('anthropic/shared-model')
    })
  })

  it('can create an additional actor profile for per-agent assignments', async () => {
    engineRequest.mockImplementation((path: string) => {
      if (path === '/models/profiles') return Promise.resolve(profiles)
      if (path === '/models/usage') {
        return Promise.resolve({
          requests: 0,
          prompt_tokens: 0,
          completion_tokens: 0,
          total_tokens: 0,
        })
      }
      if (path === '/projects/fog-harbor/model-policy') {
        return Promise.resolve(policy)
      }
      if (path === '/projects/fog-harbor/characters') {
        return Promise.resolve(characters)
      }
      if (path === '/models/profiles/actor-custom-1') {
        return Promise.resolve({ ...profiles[0], id: 'actor-custom-1' })
      }
      throw new Error(path)
    })
    render(<ModelProfiles />)
    fireEvent.click(await screen.findByRole('button', { name: '新增角色 Profile' }))

    await waitFor(() =>
      expect(engineRequest).toHaveBeenCalledWith(
        '/models/profiles/actor-custom-1',
        expect.objectContaining({ method: 'PUT' })
      )
    )
    expect(await screen.findByLabelText('角色模型（actor-custom-1）')).toBeInTheDocument()
  })
})
