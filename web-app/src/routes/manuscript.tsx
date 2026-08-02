import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { ManuscriptView } from '@/features/story/manuscript/ManuscriptView'

export const Route = createFileRoute(route.manuscript)({
  component: ManuscriptView,
})
