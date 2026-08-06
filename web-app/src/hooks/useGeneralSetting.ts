import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { localStorageKey } from '@/constants/localStorage'
import { backendStorage } from '@/lib/backendStorage'

type GeneralSettingState = {
  currentLanguage: Language
  spellCheckChatInput: boolean
  tokenCounterCompact: boolean
  autoUpdateCheck: boolean
  stripReasoningFromContext: boolean
  setSpellCheckChatInput: (value: boolean) => void
  setTokenCounterCompact: (value: boolean) => void
  setAutoUpdateCheck: (value: boolean) => void
  setStripReasoningFromContext: (value: boolean) => void
  setCurrentLanguage: (value: Language) => void
}

export const useGeneralSetting = create<GeneralSettingState>()(
  persist(
    (set) => ({
      currentLanguage: 'en',
      spellCheckChatInput: true,
      tokenCounterCompact: true,
      autoUpdateCheck: true,
      stripReasoningFromContext: false,
      setSpellCheckChatInput: (value) => set({ spellCheckChatInput: value }),
      setTokenCounterCompact: (value) => set({ tokenCounterCompact: value }),
      setAutoUpdateCheck: (value) => set({ autoUpdateCheck: value }),
      setStripReasoningFromContext: (value) =>
        set({ stripReasoningFromContext: value }),
      setCurrentLanguage: (value) => set({ currentLanguage: value }),
    }),
    {
      name: localStorageKey.settingGeneral,
      storage: createJSONStorage(() => backendStorage),
      skipHydration: true,
      partialize: (state) => ({
        currentLanguage: state.currentLanguage,
        spellCheckChatInput: state.spellCheckChatInput,
        tokenCounterCompact: state.tokenCounterCompact,
        autoUpdateCheck: state.autoUpdateCheck,
        stripReasoningFromContext: state.stripReasoningFromContext,
      }),
    }
  )
)

