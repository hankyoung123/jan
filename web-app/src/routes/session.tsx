import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { WorldSessionView } from '@/features/story/session/WorldSessionView'

export const Route = createFileRoute(route.session)({
  component: WorldSessionView,
})
