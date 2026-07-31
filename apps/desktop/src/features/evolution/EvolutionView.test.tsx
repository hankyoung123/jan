import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import { EvolutionWorkspace } from "./EvolutionView";

const candidate = {
  id: "turn-000001",
  project_id: "fog-harbor",
  base_world_version: 0,
  base_character_versions: { "chen-mo": 0, "lin-lan": 0 },
  intents: [
    {
      character_id: "chen-mo",
      action: "检查灯塔机械装置",
      goal: "查明灯塔熄灭原因",
      knowledge_basis: ["secret:chen-father-disappearance"],
    },
    {
      character_id: "lin-lan",
      action: "呼叫客船降低航速",
      goal: "让客船安全进入雾港",
      knowledge_basis: ["secret:lin-unfiled-duty-roster"],
    },
  ],
  outcome: {
    summary: "第 1 轮: 两个角色的行动共同改变了雾港局势。",
    public_results: ["雾港局势推进至第 1 轮"],
    hidden_results: [],
    character_changes: [],
    world_changes: [],
    new_npcs: [],
    unresolved_consequences: [],
  },
  review: {
    mode: "turn_review",
    passed: true,
    summary: "知识边界、世界规则和状态来源检查通过。",
    issues: [],
  },
  status: "reviewed",
} as const;

afterEach(() => vi.unstubAllGlobals());

it("renders isolated intents and only the three user decisions", async () => {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => candidate,
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();
  render(<EvolutionWorkspace />);

  await user.click(screen.getByRole("button", { name: "生成角色行动" }));

  expect(await screen.findByText("检查灯塔机械装置")).toBeInTheDocument();
  expect(screen.getByText("呼叫客船降低航速")).toBeInTheDocument();
  expect(screen.getByText(/统一结算/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "确认本轮" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "要求修改" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "放弃本轮" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /审核/ })).not.toBeInTheDocument();

  const body = JSON.parse(String(fetchMock.mock.calls[0][1].body));
  expect(body.participant_ids).toEqual(["chen-mo", "lin-lan"]);
});
