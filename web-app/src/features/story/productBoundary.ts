import { route } from '@/constants/routes'

/**
 * Jan's chat-era URLs stay routable only so old bookmarks fail safely inside
 * the Story product. They never render the inherited chat, project, or
 * assistant-management screens.
 */
export const retiredJanProductRoutes = {
  thread: {
    path: route.threadsDetail,
    destination: route.home,
  },
  project: {
    path: route.projectDetail,
    destination: route.submission,
  },
  assistant: {
    path: route.settings.assistant,
    destination: route.settings.general,
  },
} as const
