import type { components } from '@story-engine/contracts'
import type { UIMessage } from 'ai'
import { ulid } from 'ulidx'
import { create } from 'zustand'

import type { StoryMessageMetadata } from '@/lib/message-capabilities'

type SubmissionDraft = components['schemas']['SubmissionDraft']
type ProjectSnapshot = components['schemas']['ProjectSnapshot']
type SubmissionFilePart = components['schemas']['SubmissionFilePart']

const initialMissingRequirements = [
  '标题',
  '类型',
  '主题',
  '基调',
  '世界规则与公共事实',
  '初始角色 (2-4 个)',
  '初始时间、地点和起始事件',
  '世界压力或角色目标冲突',
]

export type SubmissionMessage = UIMessage<StoryMessageMetadata>

export function createSubmissionMessage(
  role: 'user' | 'assistant',
  text: string,
  metadata?: Partial<StoryMessageMetadata>,
  files: SubmissionFilePart[] = []
): SubmissionMessage {
  return {
    id: ulid(),
    role,
    parts: [
      ...(text ? [{ type: 'text' as const, text }] : []),
      ...files.map(({ filename, ...file }) => ({
        ...file,
        filename: filename ?? undefined,
      })),
    ],
    metadata: {
      callId: metadata?.callId ?? `submission:${ulid()}`,
      agentType: metadata?.agentType ?? (role === 'assistant' ? 'submission_editor' : 'user'),
      agentName: metadata?.agentName ?? (role === 'assistant' ? 'Story Editor' : 'User'),
      taskLabel: metadata?.taskLabel ?? '投稿讨论',
      ...metadata,
      promptTokens: metadata?.promptTokens ?? 0,
      completionTokens: metadata?.completionTokens ?? 0,
      outputStatus: metadata?.outputStatus ?? 'completed',
      versionGroupId: metadata?.versionGroupId,
      versionIndex: metadata?.versionIndex ?? 1,
      active: metadata?.active ?? true,
      stopped: metadata?.stopped ?? false,
      createdAt: metadata?.createdAt ?? new Date().toISOString(),
    },
  }
}

export function submissionMessageText(message: UIMessage): string {
  return message.parts
    .filter(
      (part): part is { type: 'text'; text: string } => part.type === 'text'
    )
    .map((part) => part.text)
    .join('\n')
}

export function activeSubmissionMessages(
  messages: SubmissionMessage[]
): SubmissionMessage[] {
  return messages.filter((message) => message.metadata?.active !== false)
}

function newSubmissionDraft(): SubmissionDraft {
  return {
    id: `story-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    title: '',
    genre: '',
    theme: '',
    tone: '',
    world_rules: [],
    facts: [],
    characters: [],
    initial_time: '',
    initial_location: '',
    initial_incident: '',
    pressures: [],
  }
}

type SubmissionSessionValues = {
  draft: SubmissionDraft
  messages: SubmissionMessage[]
  composer: string
  missingRequirements: string[]
  reviewSummary: string
  runnable: boolean
  project: ProjectSnapshot | null
  discussing: boolean
  saving: boolean
  error: string | null
}

type SubmissionSessionStore = SubmissionSessionValues & {
  setDraft: (draft: SubmissionDraft) => void
  setMessages: (messages: SubmissionMessage[]) => void
  setComposer: (composer: string) => void
  setMissingRequirements: (requirements: string[]) => void
  setReviewSummary: (summary: string) => void
  setRunnable: (runnable: boolean) => void
  setProject: (project: ProjectSnapshot | null) => void
  setDiscussing: (discussing: boolean) => void
  setSaving: (saving: boolean) => void
  setError: (error: string | null) => void
  reset: () => void
}

function initialSession(): SubmissionSessionValues {
  return {
    draft: newSubmissionDraft(),
    messages: [
      createSubmissionMessage(
        'assistant',
        '告诉我你想建立怎样的故事世界。我们会一起明确创作方向、世界规则、初始角色和起始局面，不需要先写大纲。'
      ),
    ],
    composer: '',
    missingRequirements: [...initialMissingRequirements],
    reviewSummary: '通过讨论逐步形成可运行的初始设定包。',
    runnable: false,
    project: null,
    discussing: false,
    saving: false,
    error: null,
  }
}

export const useSubmissionSession = create<SubmissionSessionStore>((set) => ({
  ...initialSession(),
  setDraft: (draft) => set({ draft }),
  setMessages: (messages) => set({ messages }),
  setComposer: (composer) => set({ composer }),
  setMissingRequirements: (missingRequirements) =>
    set({ missingRequirements }),
  setReviewSummary: (reviewSummary) => set({ reviewSummary }),
  setRunnable: (runnable) => set({ runnable }),
  setProject: (project) => set({ project }),
  setDiscussing: (discussing) => set({ discussing }),
  setSaving: (saving) => set({ saving }),
  setError: (error) => set({ error }),
  reset: () => set(initialSession()),
}))

export function resetSubmissionSession(): void {
  useSubmissionSession.getState().reset()
}

const ACTIVE_SUBMISSION_KEY = 'story-engine.active-submission-id'

export function activeSubmissionId(): string | null {
  return localStorage.getItem(ACTIVE_SUBMISSION_KEY)
}

export function rememberSubmission(id: string): void {
  localStorage.setItem(ACTIVE_SUBMISSION_KEY, id)
}

export function forgetSubmission(): void {
  localStorage.removeItem(ACTIVE_SUBMISSION_KEY)
}
