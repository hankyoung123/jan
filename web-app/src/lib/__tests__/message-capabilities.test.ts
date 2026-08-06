import { describe, expect, it } from 'vitest'

import {
  MESSAGE_PRESETS,
  resolveMessageCapabilities,
} from '../message-capabilities'

describe('message capability presets', () => {
  it('keeps all Jan chat capabilities enabled for submission chat', () => {
    expect(MESSAGE_PRESETS.chat.capabilities).toMatchObject({
      attachments: true,
      edit: true,
      delete: true,
      regenerate: true,
      continue: true,
      versions: true,
      citations: true,
      tools: true,
      reasoning: true,
    })
  })

  it('uses one renderer with observation-only live agent capabilities', () => {
    expect(MESSAGE_PRESETS['live-agent']).toMatchObject({
      capabilities: {
        attachments: false,
        edit: false,
        delete: false,
        tools: true,
        reasoning: true,
      },
      presentation: {
        reasoningDefaultOpen: false,
        reasoningCollapsedPreview: true,
      },
    })
  })

  it('allows a page to override one preset capability', () => {
    expect(
      resolveMessageCapabilities('readonly', { attachments: false })
    ).toMatchObject({
      attachments: false,
      edit: false,
      copy: true,
      reasoning: true,
    })
  })
})
