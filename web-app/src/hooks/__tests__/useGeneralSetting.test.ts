import { describe, it, expect, beforeEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useGeneralSetting } from '../useGeneralSetting'

// Mock constants
vi.mock('@/constants/localStorage', () => ({
  localStorageKey: {
    settingGeneral: 'general-settings',
  },
}))

// Mock zustand persist
vi.mock('zustand/middleware', () => ({
  persist: (fn: any) => fn,
  createJSONStorage: () => ({
    getItem: vi.fn(),
    setItem: vi.fn(),
    removeItem: vi.fn(),
  }),
}))

describe('useGeneralSetting', () => {
  beforeEach(() => {
    vi.clearAllMocks()

    // Reset store state to defaults
    useGeneralSetting.setState({
      currentLanguage: 'en',
      spellCheckChatInput: true,
      tokenCounterCompact: true,
    })
  })

  it('should initialize with default values', () => {
    const { result } = renderHook(() => useGeneralSetting())

    expect(result.current.currentLanguage).toBe('en')
    expect(result.current.spellCheckChatInput).toBe(true)
    expect(typeof result.current.setCurrentLanguage).toBe('function')
    expect(typeof result.current.setSpellCheckChatInput).toBe('function')
  })

  describe('setCurrentLanguage', () => {
    it('should set language to English', () => {
      const { result } = renderHook(() => useGeneralSetting())

      act(() => {
        result.current.setCurrentLanguage('en')
      })

      expect(result.current.currentLanguage).toBe('en')
    })

    it('should set language to Indonesian', () => {
      const { result } = renderHook(() => useGeneralSetting())

      act(() => {
        result.current.setCurrentLanguage('id')
      })

      expect(result.current.currentLanguage).toBe('id')
    })

    it('should set language to Vietnamese', () => {
      const { result } = renderHook(() => useGeneralSetting())

      act(() => {
        result.current.setCurrentLanguage('vn')
      })

      expect(result.current.currentLanguage).toBe('vn')
    })

    it('should change language multiple times', () => {
      const { result } = renderHook(() => useGeneralSetting())

      act(() => {
        result.current.setCurrentLanguage('id')
      })
      expect(result.current.currentLanguage).toBe('id')

      act(() => {
        result.current.setCurrentLanguage('vn')
      })
      expect(result.current.currentLanguage).toBe('vn')

      act(() => {
        result.current.setCurrentLanguage('en')
      })
      expect(result.current.currentLanguage).toBe('en')
    })
  })

  describe('setSpellCheckChatInput', () => {
    it('should enable spell check', () => {
      const { result } = renderHook(() => useGeneralSetting())

      act(() => {
        result.current.setSpellCheckChatInput(true)
      })

      expect(result.current.spellCheckChatInput).toBe(true)
    })

    it('should disable spell check', () => {
      const { result } = renderHook(() => useGeneralSetting())

      act(() => {
        result.current.setSpellCheckChatInput(false)
      })

      expect(result.current.spellCheckChatInput).toBe(false)
    })

    it('should toggle spell check multiple times', () => {
      const { result } = renderHook(() => useGeneralSetting())

      act(() => {
        result.current.setSpellCheckChatInput(false)
      })
      expect(result.current.spellCheckChatInput).toBe(false)

      act(() => {
        result.current.setSpellCheckChatInput(true)
      })
      expect(result.current.spellCheckChatInput).toBe(true)
    })
  })

  describe('state management', () => {
    it('should maintain state across multiple hook instances', () => {
      const { result: result1 } = renderHook(() => useGeneralSetting())
      const { result: result2 } = renderHook(() => useGeneralSetting())

      act(() => {
        result1.current.setCurrentLanguage('id')
        result1.current.setSpellCheckChatInput(false)
      })

      expect(result2.current.currentLanguage).toBe('id')
      expect(result2.current.spellCheckChatInput).toBe(false)
    })
  })

  describe('complex scenarios', () => {
    it('should handle complete settings configuration', () => {
      const { result } = renderHook(() => useGeneralSetting())

      act(() => {
        result.current.setCurrentLanguage('vn')
        result.current.setSpellCheckChatInput(false)
      })

      expect(result.current.currentLanguage).toBe('vn')
      expect(result.current.spellCheckChatInput).toBe(false)
    })

    it('should handle multiple sequential updates', () => {
      const { result } = renderHook(() => useGeneralSetting())

      // First update
      act(() => {
        result.current.setCurrentLanguage('id')
        result.current.setSpellCheckChatInput(false)
      })

      expect(result.current.currentLanguage).toBe('id')
      expect(result.current.spellCheckChatInput).toBe(false)

      // Second update
      act(() => {
        result.current.setCurrentLanguage('en')
        result.current.setSpellCheckChatInput(true)
      })

      expect(result.current.currentLanguage).toBe('en')
      expect(result.current.spellCheckChatInput).toBe(true)
    })
  })
})
