import { useKeyboardShortcut } from '@/hooks/useHotkeys'
import { useLeftPanel } from '@/hooks/useLeftPanel'
import { useRouter } from '@tanstack/react-router'
import { route } from '@/constants/routes'
import { PlatformShortcuts, ShortcutAction } from '@/lib/shortcuts'

export function KeyboardShortcutsProvider() {
  const { open, setLeftPanel } = useLeftPanel()
  const router = useRouter()

  // Get shortcut specs from centralized configuration
  const sidebarShortcut = PlatformShortcuts[ShortcutAction.TOGGLE_SIDEBAR]
  const newProjectShortcut = PlatformShortcuts[ShortcutAction.NEW_PROJECT]
  const settingsShortcut = PlatformShortcuts[ShortcutAction.GO_TO_SETTINGS]

  // Toggle Sidebar
  useKeyboardShortcut({
    ...sidebarShortcut,
    callback: () => {
      setLeftPanel(!open)
    },
  })

  // New Story project
  useKeyboardShortcut({
    ...newProjectShortcut,
    callback: () => {
      router.navigate({ to: route.submission })
    },
  })

  // Go to Settings
  useKeyboardShortcut({
    ...settingsShortcut,
    callback: () => {
      router.navigate({ to: route.settings.general })
    },
  })

  // This component doesn't render anything
  return null
}
