import { Link, useRouterState } from '@tanstack/react-router'

import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'
import { storyRoutes } from './navigation'

export function NavStory() {
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const { isMobile, setOpenMobile } = useSidebar()
  const closeMobileNavigation = () => {
    if (isMobile) setOpenMobile(false)
  }

  return (
    <SidebarGroup>
      <SidebarGroupLabel>雾港</SidebarGroupLabel>
      <SidebarGroupContent>
        <SidebarMenu>
          {storyRoutes.map(({ title, url, icon: Icon }) => (
            <SidebarMenuItem key={url}>
              <SidebarMenuButton asChild isActive={pathname === url}>
                <Link onClick={closeMobileNavigation} to={url}>
                  <Icon className="text-foreground/70" size={16} />
                  <span>{title}</span>
                </Link>
              </SidebarMenuButton>
            </SidebarMenuItem>
          ))}
        </SidebarMenu>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
