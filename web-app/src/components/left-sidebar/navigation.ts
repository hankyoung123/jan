import {
  BookOpenText,
  Boxes,
  CalendarClock,
  LayoutDashboard,
  MessageSquarePlus,
  PersonStanding,
  Settings,
  Sparkles,
  UsersRound,
} from 'lucide-react'

import { route } from '@/constants/routes'

export const primaryRoutes = [
  { title: '投稿', url: route.submission, icon: MessageSquarePlus },
  { title: '全局设置', url: route.settings.general, icon: Settings },
]

export const storyRoutes = [
  { title: '进入世界', url: route.session, icon: PersonStanding },
  { title: '工作台', url: route.home, icon: LayoutDashboard },
  { title: '推进故事', url: route.evolve, icon: Sparkles },
  { title: '角色', url: route.characters, icon: UsersRound },
  { title: '世界设定', url: route.world, icon: Boxes },
  { title: '事件历史', url: route.events, icon: CalendarClock },
  { title: '章节正文', url: route.manuscript, icon: BookOpenText },
]
