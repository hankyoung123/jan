import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { SubmissionView } from "./SubmissionView";

afterEach(() => vi.unstubAllGlobals());

it("finalizes a runnable setting package without an outline field", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      project: { id: "fog-harbor", title: "雾港" },
      world: { version: 0 },
      characters: [{ id: "chen-mo" }, { id: "lin-lan" }],
    }),
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<SubmissionView />);

  expect(screen.queryByLabelText(/大纲/)).not.toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "创建雾港项目" }));

  expect(await screen.findByText(/可进入第一轮/)).toBeInTheDocument();
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  expect(url).toMatch(/\/submissions\/finalize$/);
  expect(init.method).toBe("POST");
  expect(JSON.parse(String(init.body))).not.toHaveProperty("outline");
});
