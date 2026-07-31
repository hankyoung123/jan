import {
  BookOpenText,
  Boxes,
  CalendarClock,
  Clapperboard,
  LayoutDashboard,
  Settings2,
  Sparkles,
  UsersRound,
  type LucideIcon,
} from "lucide-react";

import {
  CharactersView,
  EventsView,
  EvolutionView,
  ManuscriptView,
  ModelsView,
  SettingsView,
  WorkbenchView,
  WorldView,
} from "../views";

export interface ProductRoute {
  path: string;
  label: string;
  icon: LucideIcon;
  element: React.ReactNode;
}

export const productRoutes: ProductRoute[] = [
  {
    path: "/",
    label: "工作台",
    icon: LayoutDashboard,
    element: <WorkbenchView />,
  },
  {
    path: "/evolve",
    label: "推进故事",
    icon: Sparkles,
    element: <EvolutionView />,
  },
  {
    path: "/characters",
    label: "角色",
    icon: UsersRound,
    element: <CharactersView />,
  },
  {
    path: "/world",
    label: "世界设定",
    icon: Boxes,
    element: <WorldView />,
  },
  {
    path: "/events",
    label: "事件历史",
    icon: CalendarClock,
    element: <EventsView />,
  },
  {
    path: "/manuscript",
    label: "章节正文",
    icon: BookOpenText,
    element: <ManuscriptView />,
  },
  {
    path: "/models",
    label: "模型中心",
    icon: Clapperboard,
    element: <ModelsView />,
  },
  {
    path: "/settings",
    label: "项目设置",
    icon: Settings2,
    element: <SettingsView />,
  },
];

