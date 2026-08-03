import type { Attachment } from '@/types/attachment'

export type UploadResult = {
  id: string
  url?: string
  size?: number
  chunkCount?: number
}

export interface UploadsService {
  // Ingest an image attachment (placeholder upload)
  ingestImage(threadId: string, attachment: Attachment): Promise<UploadResult>

}
