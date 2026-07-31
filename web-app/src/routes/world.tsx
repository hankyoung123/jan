import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { WorldView } from '@/features/story/StoryViews'

export const Route = createFileRoute(route.world)({
  component: WorldView,
})
