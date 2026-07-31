import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "./App";

const navigation = [
  "工作台",
  "推进故事",
  "角色",
  "世界设定",
  "事件历史",
  "章节正文",
  "模型中心",
  "项目设置",
];

function renderApp(initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AppShell />
    </MemoryRouter>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AppShell", () => {
  it("renders every product destination and navigates between pages", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("engine offline")),
    );
    const user = userEvent.setup();
    renderApp();

    for (const label of navigation) {
      expect(
        screen.getByRole("link", { name: new RegExp(label) }),
      ).toBeInTheDocument();
    }

    await user.click(screen.getByRole("link", { name: /角色/ }));
    expect(
      screen.getByRole("heading", { name: "角色", level: 1 }),
    ).toBeInTheDocument();
  });

  it("shows a connected engine status after a successful health check", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          status: "ok",
          service: "story-engine",
          version: "0.1.0",
        }),
      }),
    );
    renderApp();

    await waitFor(() => {
      expect(screen.getByText("引擎已连接")).toBeInTheDocument();
    });
  });
});

