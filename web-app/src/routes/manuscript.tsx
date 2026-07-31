import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { ManuscriptView } from '@/features/story/StoryViews'

export const Route = createFileRoute(route.manuscript)({
  component: ManuscriptView,
})
