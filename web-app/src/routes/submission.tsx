import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { SubmissionView } from '@/features/story/StoryViews'

export const Route = createFileRoute(route.submission)({
  component: SubmissionView,
})
