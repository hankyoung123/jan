import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { SimulationHistoryView } from '@/features/story/history/SimulationHistoryView'

export const Route = createFileRoute(route.events)({
  component: SimulationHistoryView,
})
