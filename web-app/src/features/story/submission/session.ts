import type { components } from '@story-engine/contracts'
import { create } from 'zustand'

type SubmissionDraft = components['schemas']['SubmissionDraft']
type SubmissionMessage = components['schemas']['Message']
type ProjectSnapshot = components['schemas']['ProjectSnapshot']

const initialMissingRequirements = [
  '创作方向',
  '世界规则与公共事实',
  '初始角色 (2-4 个)',
  '初始时间、地点和起始事件',
  '世界压力或角色目标冲突',
]

const welcomeMessage: SubmissionMessage = {
  role: 'assistant',
  content:
    '告诉我你想建立怎样的故事世界。我们会一起明确创作方向、世界规则、初始角色和起始局面，不需要先写大纲。',
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
    messages: [welcomeMessage],
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
