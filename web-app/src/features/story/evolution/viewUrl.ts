import type { SimulationStage } from '@story-engine/contracts'

export interface ViewLocation {
  step?: number
  stage?: SimulationStage
  actor?: string
  panel?: string
}

const PANEL_VALUES = new Set(['trace', 'memory'])

export function readViewLocation(search: string): ViewLocation {
  const params = new URLSearchParams(search)
  const step = params.get('step')
  const stage = params.get('stage')
  const actor = params.get('actor')
  const panel = params.get('panel')
  return {
    step: step !== null && /^\d+$/.test(step) ? Number(step) : undefined,
    stage: stage ? (stage as SimulationStage) : undefined,
    actor: actor ?? undefined,
    panel: panel !== null && PANEL_VALUES.has(panel) ? panel : undefined,
  }
}

export function applyViewLocation(
  search: string,
  location: ViewLocation
): string {
  const params = new URLSearchParams(search)
  if (location.step !== undefined) params.set('step', String(location.step))
  else params.delete('step')
  if (location.stage !== undefined) params.set('stage', location.stage)
  else params.delete('stage')
  if (location.actor !== undefined) params.set('actor', location.actor)
  else params.delete('actor')
  if (location.panel !== undefined) params.set('panel', location.panel)
  else params.delete('panel')
  return params.toString()
}
