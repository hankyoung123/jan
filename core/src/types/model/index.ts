/** Model selection persisted with a thread or assistant. */
export type ModelInfo = {
  id: string
  engine?: string
  settings?: Record<string, unknown>
  parameters?: Record<string, unknown>
}
