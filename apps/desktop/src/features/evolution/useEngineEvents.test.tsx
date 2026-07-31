import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useEngineEvents } from "./useEngineEvents";

class FakeWebSocket {
  static instance: FakeWebSocket | null = null;
  onopen: (() => void) | null = null;
  onmessage: ((message: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  readonly protocols: string[];
  readonly url: string;

  constructor(url: string, protocols: string[]) {
    this.url = url;
    this.protocols = protocols;
    FakeWebSocket.instance = this;
  }

  close() {
    this.onclose?.();
  }

  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  FakeWebSocket.instance = null;
});

it("authenticates via subprotocol and accepts typed project events", async () => {
  vi.stubGlobal("WebSocket", FakeWebSocket);
  const { result } = renderHook(() => useEngineEvents("fog-harbor"));
  await waitFor(() => expect(FakeWebSocket.instance).not.toBeNull());
  const socket = FakeWebSocket.instance;
  expect(socket).not.toBeNull();
  expect(socket?.url).toContain("project_id=fog-harbor");
  expect(socket?.url).not.toContain("development-token");
  expect(socket?.protocols).toContain("story-engine.v1");
  expect(socket?.protocols).toContain("story-engine.token.development-token");

  act(() => {
    socket?.emit({
      event_id: "01K1G9HH000000000000000000",
      project_id: "fog-harbor",
      turn_id: "turn-000001",
      timestamp: "2026-07-31T06:00:00Z",
      type: "resolver.completed",
      payload: {},
    });
  });

  expect(result.current.latest?.type).toBe("resolver.completed");
  expect(result.current.connected).toBe(true);
});
