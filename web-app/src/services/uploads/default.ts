import type { UploadsService, UploadResult } from './types'
import type { Attachment } from '@/types/attachment'
import { ulid } from 'ulidx'

export class DefaultUploadsService implements UploadsService {
  async ingestImage(_threadId: string, attachment: Attachment): Promise<UploadResult> {
    if (attachment.type !== 'image') throw new Error('ingestImage: attachment is not image')
    // Placeholder upload flow; swap for real API call when backend is ready
    await new Promise((r) => setTimeout(r, 100))
    return { id: ulid() }
  }
}
