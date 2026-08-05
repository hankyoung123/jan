import { createFileRoute } from '@tanstack/react-router'

import { route } from '@/constants/routes'
import HeaderPage from '@/containers/HeaderPage'
import SettingsMenu from '@/containers/SettingsMenu'
import { ModelProfiles } from '@/features/story/ModelProfiles'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const Route = createFileRoute(route.settings.story_models as any)({
  component: StoryModelSettings,
})

function StoryModelSettings() {
  return (
    <div className="flex h-svh w-full flex-col">
      <HeaderPage>
        <span className="block pl-10 font-studio text-base font-medium md:pl-0">
          Story Agent 模型
        </span>
      </HeaderPage>
      <div className="flex min-h-0 flex-1 flex-col md:flex-row">
        <SettingsMenu />
        <main className="w-full overflow-y-auto p-4 pt-0">
          <ModelProfiles />
        </main>
      </div>
    </div>
  )
}
