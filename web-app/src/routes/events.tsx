import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { EventsView } from '@/features/story/StoryViews'

export const Route = createFileRoute(route.events)({
  component: EventsView,
})
