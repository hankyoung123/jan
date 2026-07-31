import { beforeEach, describe, expect, it } from 'vitest'

import {
  clearActiveStoryProject,
  getActiveStoryProjectId,
  setActiveStoryProjectId,
} from './activeProject'

describe('active Story Engine project selection', () => {
  beforeEach(() => clearActiveStoryProject())

  it('persists only a validated project identifier', () => {
    setActiveStoryProjectId('north-star')

    expect(getActiveStoryProjectId()).toBe('north-star')
    expect(localStorage.getItem('story-engine.active-project-id')).toBe(
      'north-star'
    )
  })

  it('rejects paths and arbitrary state payloads', () => {
    expect(() => setActiveStoryProjectId('../north-star')).toThrow(
      'invalid Story Engine project ID'
    )
    expect(() => setActiveStoryProjectId('{"world":{"version":4}}')).toThrow(
      'invalid Story Engine project ID'
    )
  })
})
