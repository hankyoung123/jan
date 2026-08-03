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
  beforeEach(() => {
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
        if (options?.method === 'PUT') return Promise.resolve(JSON.parse(String(options.body)))
        return Promise.resolve(policy)
      }
      if (path === '/projects/fog-harbor/characters') return Promise.resolve(characters)
      if (path === '/models/profiles/actor') {
        return Promise.resolve({ ...profiles[0], model_ref: 'openai/shared-model' })
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

  it('saves the provider-qualified model reference', async () => {
    render(<ModelProfiles />)
    fireEvent.change(await screen.findByLabelText('角色模型'), {
      target: { value: 'openai/shared-model' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存角色模型' }))

    await waitFor(() =>
      expect(engineRequest).toHaveBeenCalledWith(
        '/models/profiles/actor',
        expect.anything()
      )
    )
    const options = engineRequest.mock.calls.find(
      ([path]) => path === '/models/profiles/actor'
    )?.[1]
    expect(JSON.parse(options.body).model_ref).toBe('openai/shared-model')
  })

  it('saves the selected thinking intensity', async () => {
    render(<ModelProfiles />)
    fireEvent.click(await screen.findByRole('button', { name: '角色模型高级参数' }))
    fireEvent.change(await screen.findByLabelText('角色模型思考强度'), {
      target: { value: 'medium' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存角色模型' }))

    await waitFor(() =>
      expect(engineRequest).toHaveBeenCalledWith(
        '/models/profiles/actor',
        expect.anything()
      )
    )
    const options = engineRequest.mock.calls.find(
      ([path]) => path === '/models/profiles/actor'
    )?.[1]
    expect(JSON.parse(options.body).reasoning_effort).toBe('medium')
  })

  it('persists a per-character actor profile override', async () => {
    render(<ModelProfiles />)
    fireEvent.change(await screen.findByLabelText('陈默 Agent Profile'), {
      target: { value: 'actor-precise' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存陈默 Agent Profile' }))

    await waitFor(() =>
      expect(engineRequest).toHaveBeenCalledWith(
        '/projects/fog-harbor/model-policy',
        expect.objectContaining({ method: 'PUT' })
      )
    )
    const options = engineRequest.mock.calls.find(
      ([path, request]) =>
        path === '/projects/fog-harbor/model-policy' && request?.method === 'PUT'
    )?.[1]
    expect(JSON.parse(options.body).agent_profile_ids).toEqual({
      'chen-mo': 'actor-precise',
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
      if (path === '/projects/fog-harbor/model-policy') return Promise.resolve(policy)
      if (path === '/projects/fog-harbor/characters') return Promise.resolve(characters)
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
