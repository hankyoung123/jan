import { createFileRoute, redirect } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { retiredJanProductRoutes } from '@/features/story/productBoundary'

export const Route = createFileRoute(route.projectDetail)({
  beforeLoad: () => {
    throw redirect({ to: retiredJanProductRoutes.project.destination })
  },
})
