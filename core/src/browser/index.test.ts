import { describe, it, expect } from 'vitest'
import * as Core from './core'
import * as Events from './events'
import * as FileSystem from './fs'
import * as Extension from './extension'
import * as Extensions from './extensions'

describe('Module Tests', () => {
  it('should export Core module', () => {
    expect(Core).toBeDefined()
  })

  it('should export Event module', () => {
    expect(Events).toBeDefined()
  })

  it('should export Filesystem module', () => {
    expect(FileSystem).toBeDefined()
  })

  it('should export Extension module', () => {
    expect(Extension).toBeDefined()
  })

  it('should export all base extensions', () => {
    expect(Extensions).toBeDefined()
  })

})
