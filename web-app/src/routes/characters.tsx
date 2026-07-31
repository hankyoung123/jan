import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import { CharactersView } from '@/features/story/StoryViews'

export const Route = createFileRoute(route.characters)({
  component: CharactersView,
})
