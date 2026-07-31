import {
  Activity,
  BookOpen,
  ChevronLeft,
  Menu,
  Moon,
  PanelRightClose,
  PanelRightOpen,
  Settings,
  Sun,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";
import {
  HashRouter,
  NavLink,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";

import { useEngineHealth } from "../hooks/useEngineHealth";
import { productRoutes } from "./routes";

type Theme = "light" | "dark";

function EngineStatus() {
  const health = useEngineHealth();
  const copy = {
    checking: "正在连接引擎",
    connected: "引擎已连接",
    offline: "引擎未连接",
  }[health.state];

  return (
    <div className={`engine-status engine-${health.state}`}>
      <Activity size={15} />
      <span>
        <strong>{copy}</strong>
        <small>{health.version ? `v${health.version}` : "Story Engine"}</small>
      </span>
    </div>
  );
}

function Inspector({ path }: { path: string }) {
  const isSubmission = path === "/submission";
  const isEvolution = path === "/evolve";
  const version = isSubmission ? "未创建" : isEvolution ? "待确认" : "12";
  const versionDetail = isSubmission
    ? "确认投稿后创建世界版本 0"
    : isEvolution
      ? "候选不会改变正式版本"
      : "最后提交 event-000012";
  const contextCount = isSubmission || isEvolution ? 2 : 3;
  return (
    <aside className="inspector" aria-label="当前上下文">
      <header>
        <div>
          <p className="section-kicker">上下文</p>
          <h2>当前回合</h2>
        </div>
        <span className="status-dot" title="状态已同步" />
      </header>
      <section>
        <h3>世界版本</h3>
        <p className={`inspector-value ${isSubmission || isEvolution ? "inspector-text-value" : ""}`}>{version}</p>
        <small>{versionDetail}</small>
      </section>
      <section>
        <h3>当前压力</h3>
        <ul className="inspector-list">
          <li>
            <span className="dot dot-red" />
            海燕号将在 40 分钟后抵港
          </li>
          <li>
            <span className="dot dot-amber" />
            暴雨持续增强
          </li>
          <li>
            <span className="dot dot-neutral" />
            备用航标仅剩 32% 电量
          </li>
        </ul>
      </section>
      <section>
        <h3>知识隔离</h3>
        <div className="privacy-check">
          <BookOpen size={15} />
          <span>
            <strong>{contextCount} 个私有上下文</strong>
            <small>未检测到跨角色泄漏</small>
          </span>
        </div>
      </section>
    </aside>
  );
}

export function AppShell() {
  const [theme, setTheme] = useState<Theme>(() => {
    const stored = window.localStorage.getItem("story-engine-theme");
    return stored === "dark" ? "dark" : "light";
  });
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const location = useLocation();

  useEffect(() => {
    setSidebarOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    window.localStorage.setItem("story-engine-theme", theme);
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  return (
    <div
      className="app-shell"
      data-inspector-open={inspectorOpen}
      data-sidebar-open={sidebarOpen}
      data-theme={theme}
    >
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>
      <aside className="sidebar">
        <header className="brand">
          <span className="brand-mark">SE</span>
          <span>
            <strong>Story Engine</strong>
            <small>雾港</small>
          </span>
          <button
            className="icon-button sidebar-close"
            onClick={() => setSidebarOpen(false)}
            title="关闭导航"
            type="button"
          >
            <X size={17} />
            <span className="sr-only">关闭导航</span>
          </button>
        </header>
        <nav aria-label="项目导航" className="primary-nav">
          <p className="nav-label">项目</p>
          {productRoutes.map(({ icon: Icon, label, path }) => (
            <NavLink
              className={({ isActive }) => (isActive ? "active" : "")}
              end={path === "/"}
              key={path}
              to={path}
            >
              <Icon size={17} strokeWidth={1.8} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <footer className="sidebar-footer">
          <EngineStatus />
          <button className="account-button" type="button">
            <span className="avatar avatar-ink">HY</span>
            <span>
              <strong>创作者</strong>
              <small>全局设置</small>
            </span>
            <Settings size={15} />
          </button>
        </footer>
      </aside>

      <div
        aria-hidden="true"
        className="sidebar-scrim"
        onClick={() => setSidebarOpen(false)}
      />

      <div className="workspace">
        <header className="titlebar" data-tauri-drag-region>
          <div className="titlebar-left">
            <button
              className="icon-button menu-button"
              onClick={() => setSidebarOpen(true)}
              title="打开导航"
              type="button"
            >
              <Menu size={18} />
              <span className="sr-only">打开导航</span>
            </button>
            <ChevronLeft size={16} />
            <span>雾港</span>
            <span className="titlebar-separator">/</span>
            <span className="titlebar-current">
              {productRoutes.find((route) => route.path === location.pathname)
                ?.label ?? "工作台"}
            </span>
          </div>
          <div className="titlebar-actions">
            <button
              className="icon-button"
              onClick={() =>
                setTheme((value) => (value === "light" ? "dark" : "light"))
              }
              title={theme === "light" ? "切换深色主题" : "切换浅色主题"}
              type="button"
            >
              {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
              <span className="sr-only">切换主题</span>
            </button>
            <button
              className="icon-button"
              onClick={() => setInspectorOpen((value) => !value)}
              title={inspectorOpen ? "收起上下文" : "展开上下文"}
              type="button"
            >
              {inspectorOpen ? (
                <PanelRightClose size={17} />
              ) : (
                <PanelRightOpen size={17} />
              )}
              <span className="sr-only">切换上下文面板</span>
            </button>
          </div>
        </header>

        <main id="main-content">
          <Routes>
            {productRoutes.map(({ element, path }) => (
              <Route element={element} key={path} path={path} />
            ))}
          </Routes>
        </main>
      </div>

      {inspectorOpen && <Inspector path={location.pathname} />}
    </div>
  );
}

export function App() {
  return (
    <HashRouter>
      <AppShell />
    </HashRouter>
  );
}
