/** Cloud-only Vercel AI SDK model factory. */
import { createAnthropic } from '@ai-sdk/anthropic'
import { createGoogleGenerativeAI } from '@ai-sdk/google'
import { createMistral } from '@ai-sdk/mistral'
import { createOpenAI } from '@ai-sdk/openai'
import { createOpenAICompatible } from '@ai-sdk/openai-compatible'
import { createXai } from '@ai-sdk/xai'
import { fetch as tauriFetch } from '@tauri-apps/plugin-http'
import {
  extractReasoningMiddleware,
  wrapLanguageModel,
  type LanguageModel,
} from 'ai'
import { isPlatformTauri } from '@/lib/platform/utils'
import { providerRemoteApiKeyChain } from '@/lib/provider-api-keys'
import {
  getProviderApiType,
  getMutualExclusionDrops,
  isModelLevelRejected,
} from '@/lib/providerCaps'
import { ensureAnthropicHeaders } from '@/lib/remoteModelCatalog'
import { splitAudioSentinels } from '@/lib/audio-sentinel'
import { splitVideoSentinels } from '@/lib/video-sentinel'

export interface ModelParameters {
  temperature?: number
  top_k?: number
  top_p?: number
  repeat_penalty?: number
  max_output_tokens?: number
  max_context_tokens?: number
  auto_compact?: boolean
  presence_penalty?: number
  frequency_penalty?: number
  stop_sequences?: string[]
}

const CLIENT_ONLY_KEYS = new Set([
  'ctx_len',
  'max_context_tokens',
  'auto_compact',
])

function runtimeFetch(): typeof globalThis.fetch {
  const runtime = globalThis as typeof globalThis & {
    __TAURI__?: unknown
    __TAURI_INTERNALS__?: unknown
  }
  const inTauri = runtime.__TAURI__ !== undefined || runtime.__TAURI_INTERNALS__ !== undefined
  return isPlatformTauri() && inTauri
    ? (tauriFetch as typeof globalThis.fetch)
    : globalThis.fetch
}

function apiKey(provider: ProviderObject, keys: string[]): string {
  const value = keys[0] ?? provider.api_key?.trim()
  if (!value) {
    throw new Error(
      `No API key configured for ${provider.provider}. Add one in Settings > Model Providers.`
    )
  }
  return value
}

function filteredParameters(
  provider: ProviderObject,
  modelId: string,
  parameters: Record<string, unknown>
): Record<string, unknown> {
  const drop = getMutualExclusionDrops(
    parameters,
    provider.provider,
    getProviderApiType(provider)
  )
  const result: Record<string, unknown> = {}
  for (const [key, value] of Object.entries(parameters)) {
    if (value === undefined || CLIENT_ONLY_KEYS.has(key) || drop.has(key)) continue
    if (isModelLevelRejected(key, provider.provider, modelId)) continue
    result[key === 'max_output_tokens' ? 'max_tokens' : key] = value
  }
  return result
}

type AuthStyle = 'bearer' | 'anthropic' | 'google'

type WirePart = { type?: string; text?: string; [key: string]: unknown }
type WireMessage = { role?: string; content?: string | WirePart[] }

function decodeSentinelsInBody(
  body: Record<string, unknown>,
  split: (text: string) => WirePart[] | null
): void {
  if (!Array.isArray(body.messages)) return
  for (const candidate of body.messages) {
    if (!candidate || typeof candidate !== 'object') continue
    const message = candidate as WireMessage
    if (message.role !== 'user') continue
    if (typeof message.content === 'string') {
      message.content = split(message.content) ?? message.content
      continue
    }
    if (!Array.isArray(message.content)) continue
    message.content = message.content.flatMap((part) => {
      if (part.type !== 'text' || typeof part.text !== 'string') return [part]
      return split(part.text) ?? [part]
    })
  }
}

export function decodeAudioSentinelsInBody(body: Record<string, unknown>): void {
  decodeSentinelsInBody(body, splitAudioSentinels)
}

export function decodeVideoSentinelsInBody(body: Record<string, unknown>): void {
  decodeSentinelsInBody(body, splitVideoSentinels)
}

export function stripAssistantReasoningInBody(body: Record<string, unknown>): void {
  if (!Array.isArray(body.messages)) return
  for (const candidate of body.messages) {
    if (!candidate || typeof candidate !== 'object') continue
    const message = candidate as Record<string, unknown>
    if (message.role !== 'assistant') continue
    delete message.reasoning
    delete message.reasoning_content
  }
}

function withRequestDefaultsAndKeyRotation(
  provider: ProviderObject,
  modelId: string,
  parameters: Record<string, unknown>,
  authStyle: AuthStyle
): typeof globalThis.fetch {
  const baseFetch = runtimeFetch()
  const keys = providerRemoteApiKeyChain(provider)
  const defaults = filteredParameters(provider, modelId, parameters)

  return async (input: RequestInfo | URL, init?: RequestInit) => {
    const requestInit: RequestInit = { ...init }
    if (typeof requestInit.body === 'string') {
      try {
        const body = JSON.parse(requestInit.body) as Record<string, unknown>
        const merged = { ...defaults, ...body }
        stripAssistantReasoningInBody(merged)
        decodeAudioSentinelsInBody(merged)
        decodeVideoSentinelsInBody(merged)
        requestInit.body = JSON.stringify(merged)
      } catch {
        // Non-JSON bodies pass through unchanged.
      }
    }

    const attempts = Math.max(keys.length, 1)
    let response: Response | undefined
    for (let index = 0; index < attempts; index += 1) {
      const headers = new Headers(requestInit.headers)
      const key = keys[index] ?? provider.api_key?.trim()
      if (key) {
        if (authStyle === 'anthropic') headers.set('x-api-key', key)
        else if (authStyle === 'google') headers.set('x-goog-api-key', key)
        else headers.set('Authorization', `Bearer ${key}`)
      }
      response = await baseFetch(input, { ...requestInit, headers })
      if (![401, 403, 429].includes(response.status) || index === attempts - 1) {
        return response
      }
    }
    return response!
  }
}

function customHeaders(provider: ProviderObject): Record<string, string> {
  return Object.fromEntries(
    (provider.custom_header ?? []).map(({ header, value }) => [header, value])
  )
}

function reasoningTag(modelId: string): string {
  return modelId.toLowerCase().includes('gemma') ? 'thought' : 'think'
}

export class ModelFactory {
  static async createModel(
    modelId: string,
    provider: ProviderObject,
    parameters: Record<string, unknown> = {}
  ): Promise<LanguageModel> {
    const providerName = provider.provider.toLowerCase()
    const apiType = getProviderApiType(provider)

    if (apiType === 'anthropic' || providerName === 'anthropic') {
      return this.anthropic(modelId, provider, parameters)
    }
    if (providerName === 'openai') {
      return this.openAI(modelId, provider, parameters)
    }
    if (providerName === 'google' || providerName === 'gemini') {
      return this.google(modelId, provider, parameters)
    }
    if (providerName === 'mistral') {
      return this.mistral(modelId, provider, parameters)
    }
    if (providerName === 'xai') {
      return this.xai(modelId, provider, parameters)
    }
    return this.openAICompatible(modelId, provider, parameters)
  }

  private static anthropic(
    modelId: string,
    provider: ProviderObject,
    parameters: Record<string, unknown>
  ): LanguageModel {
    const headers = customHeaders(provider)
    ensureAnthropicHeaders(provider, headers)
    const keys = providerRemoteApiKeyChain(provider)
    const client = createAnthropic({
      apiKey: apiKey(provider, keys),
      baseURL: provider.base_url,
      headers,
      fetch: withRequestDefaultsAndKeyRotation(provider, modelId, parameters, 'anthropic'),
    })
    return client(modelId)
  }

  private static openAI(
    modelId: string,
    provider: ProviderObject,
    parameters: Record<string, unknown>
  ): LanguageModel {
    const keys = providerRemoteApiKeyChain(provider)
    const client = createOpenAI({
      apiKey: apiKey(provider, keys),
      baseURL: provider.base_url,
      headers: customHeaders(provider),
      fetch: withRequestDefaultsAndKeyRotation(provider, modelId, parameters, 'bearer'),
    })
    return client.responses(modelId)
  }

  private static google(
    modelId: string,
    provider: ProviderObject,
    parameters: Record<string, unknown>
  ): LanguageModel {
    const keys = providerRemoteApiKeyChain(provider)
    const rawBase = provider.base_url?.trim()
    const baseURL = rawBase
      ? rawBase.replace(/\/openai\/?$/, '').replace(/\/$/, '')
      : 'https://generativelanguage.googleapis.com/v1beta'
    const client = createGoogleGenerativeAI({
      apiKey: apiKey(provider, keys),
      baseURL,
      headers: customHeaders(provider),
      fetch: withRequestDefaultsAndKeyRotation(provider, modelId, parameters, 'google'),
    })
    return client(modelId)
  }

  private static mistral(
    modelId: string,
    provider: ProviderObject,
    parameters: Record<string, unknown>
  ): LanguageModel {
    const keys = providerRemoteApiKeyChain(provider)
    const client = createMistral({
      apiKey: apiKey(provider, keys),
      baseURL: provider.base_url,
      headers: customHeaders(provider),
      fetch: withRequestDefaultsAndKeyRotation(provider, modelId, parameters, 'bearer'),
    })
    return client(modelId)
  }

  private static xai(
    modelId: string,
    provider: ProviderObject,
    parameters: Record<string, unknown>
  ): LanguageModel {
    const keys = providerRemoteApiKeyChain(provider)
    const client = createXai({
      apiKey: apiKey(provider, keys),
      baseURL: provider.base_url,
      headers: customHeaders(provider),
      fetch: withRequestDefaultsAndKeyRotation(provider, modelId, parameters, 'bearer'),
    })
    return client(modelId)
  }

  private static openAICompatible(
    modelId: string,
    provider: ProviderObject,
    parameters: Record<string, unknown>
  ): LanguageModel {
    const keys = providerRemoteApiKeyChain(provider)
    const client = createOpenAICompatible({
      name: provider.provider,
      baseURL: provider.base_url || 'https://api.openai.com/v1',
      apiKey: apiKey(provider, keys),
      headers: customHeaders(provider),
      includeUsage: true,
      fetch: withRequestDefaultsAndKeyRotation(provider, modelId, parameters, 'bearer'),
    })
    return wrapLanguageModel({
      model: client.languageModel(modelId),
      middleware: extractReasoningMiddleware({
        tagName: reasoningTag(modelId),
        separator: '\n',
      }),
    })
  }
}
