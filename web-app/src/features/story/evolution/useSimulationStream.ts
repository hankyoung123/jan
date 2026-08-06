import type {
  components,
  EngineEventEnvelope,
  SimulationStage,
} from '@story-engine/contracts'
import { useCallback, useEffect, useState } from 'react'

import { engineRequest, subscribeProjectEvents } from '../engine'
import {
  initialSimulationViewState,
  reduceSimulationEvent,
  restoreSimulationTrace,
  selectStage,
  selectViewLocation,
  type SimulationViewState,
} from './simulationViewModel'
import type { ViewLocation } from './viewUrl'

interface UseSimulationStreamOptions {
  projectId?: string
  sessionId?: string
  sessionStatus?: string
  currentStep?: number
  branchId?: string
  onSessionChanged?: () => void
  initialLocation?: ViewLocation
  onLocationChange?: (location: ViewLocation) => void
}

export function useSimulationStream({
  projectId,
  sessionId,
  sessionStatus,
  currentStep,
  branchId,
  onSessionChanged,
  initialLocation,
  onLocationChange,
}: UseSimulationStreamOptions) {
  const [viewState, setViewState] = useState<SimulationViewState>(
    initialSimulationViewState
  )
  const [revision, setRevision] = useState(0)
  const applyEvent = useCallback(
    (event: EngineEventEnvelope) => {
      if (sessionId && event.subject_id !== sessionId && event.subject_id !== 'stream') {
        return
      }
      if (event.type === 'stream.resync_required') {
        onSessionChanged?.()
        setRevision((current) => current + 1)
        return
      }
      setViewState((current) => reduceSimulationEvent(current, event))
      if (
        event.type === 'simulation.step.completed' ||
        event.type === 'simulation.paused' ||
        event.type === 'simulation.maintenance.degraded' ||
        event.type === 'simulation.terminated' ||
        event.type === 'simulation.failed' ||
        event.type === 'simulation.checkpointed'
      ) {
        onSessionChanged?.()
      }
    },
    [onSessionChanged, sessionId]
  )

  useEffect(() => {
    if (!projectId || !sessionId) {
      setViewState(initialSimulationViewState)
      return
    }
    const activeProjectId = projectId
    const activeSessionId = sessionId
    let disposed = false
    let unsubscribe: (() => void) | undefined

    async function connect() {
      const trace = branchId
        ? await engineRequest<components['schemas']['SimulationLogRecord'][]>(
            `/projects/${activeProjectId}/branches/${branchId}/simulation-trace?after_step=-1`
          )
        : []
      let history: EngineEventEnvelope[] = []
      try {
        history = await engineRequest<EngineEventEnvelope[]>(
          `/projects/${activeProjectId}/simulation-events?after_sequence=0`
        )
      } catch {
        // Durable trace remains canonical after the bounded event window expires.
      }
      if (disposed) return
      let restored = restoreSimulationTrace(
        trace,
        activeSessionId,
        activeProjectId,
        sessionStatus,
        currentStep
      )
      for (const event of history) {
        if (event.subject_id === activeSessionId) {
          restored = reduceSimulationEvent(restored, event)
        } else {
          restored = {
            ...restored,
            lastSequence: Math.max(restored.lastSequence, event.sequence),
          }
        }
      }
      if (initialLocation) {
        restored = selectViewLocation(restored, initialLocation)
      }
      setViewState(restored)
      unsubscribe = await subscribeProjectEvents(
        activeProjectId,
        applyEvent,
        restored.lastSequence
      )
    }

    void connect().catch(onSessionChanged)
    return () => {
      disposed = true
      unsubscribe?.()
    }
  }, [
    applyEvent,
    branchId,
    currentStep,
    initialLocation,
    onSessionChanged,
    projectId,
    revision,
    sessionId,
    sessionStatus,
  ])

  const chooseStage = useCallback((step: number, stage: SimulationStage) => {
    setViewState((current) => selectStage(current, step, stage))
    onLocationChange?.({ step, stage })
  }, [onLocationChange])

  const applyLocation = useCallback((location: ViewLocation) => {
    setViewState((current) => selectViewLocation(current, location))
  }, [])

  return { viewState, chooseStage, applyLocation }
}
