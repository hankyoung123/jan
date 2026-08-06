import { createFileRoute, redirect } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { retiredJanProductRoutes } from '@/features/story/productBoundary'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const Route = createFileRoute(route.settings.assistant as any)({
  beforeLoad: () => {
    throw redirect({ to: retiredJanProductRoutes.assistant.destination })
  },
})
