import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('ulidx', () => ({ ulid: vi.fn(() => 'mock-ulid-123') }))

import { DefaultUploadsService } from '../default'
import type { Attachment } from '@/types/attachment'

beforeEach(() => vi.useFakeTimers())

describe('DefaultUploadsService.ingestImage', () => {
  it('returns an id for image attachments', async () => {
    const attachment = {
      type: 'image',
      name: 'img.png',
      path: '/img.png',
      size: 100,
      fileType: 'png',
    } as Attachment
    const promise = new DefaultUploadsService().ingestImage('t1', attachment)
    await vi.advanceTimersByTimeAsync(200)
    await expect(promise).resolves.toEqual({ id: 'mock-ulid-123' })
  })

  it('rejects non-image media', async () => {
    const attachment = {
      type: 'audio',
      name: 'clip.mp3',
      path: '/clip.mp3',
      size: 100,
      fileType: 'mp3',
    } as Attachment
    await expect(
      new DefaultUploadsService().ingestImage('t1', attachment)
    ).rejects.toThrow('not image')
  })
})
