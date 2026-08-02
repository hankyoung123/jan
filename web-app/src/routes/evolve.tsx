import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { EvolutionView } from '@/features/story/evolution/EvolutionView'

export const Route = createFileRoute(route.evolve)({
  component: EvolutionView,
})
