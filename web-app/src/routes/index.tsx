import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { WorkbenchView } from '@/features/story/StoryViews'

export const Route = createFileRoute(route.home)({
  component: WorkbenchView,
})
