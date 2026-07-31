import { Link, useRouterState } from '@tanstack/react-router'

import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'
import { primaryRoutes } from './navigation'

export function NavMain() {
  const pathname = useRouterState({ select: (state) => state.location.pathname })
  const { isMobile, setOpenMobile } = useSidebar()
  const closeMobileNavigation = () => {
    if (isMobile) setOpenMobile(false)
  }

  return (
    <SidebarMenu>
      {primaryRoutes.map(({ title, url, icon: Icon }) => (
        <SidebarMenuItem key={url}>
          <SidebarMenuButton asChild isActive={pathname.startsWith(url)}>
            <Link onClick={closeMobileNavigation} to={url}>
              <Icon className="text-foreground/70" size={16} />
              <span>{title}</span>
            </Link>
          </SidebarMenuButton>
        </SidebarMenuItem>
      ))}
    </SidebarMenu>
  )
}
