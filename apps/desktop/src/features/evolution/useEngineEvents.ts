import {
  isEngineEvent,
  type EngineEventEnvelope,
} from "@story-engine/contracts";
import { useEffect, useState } from "react";

import { resolveEngineRuntime } from "../api/runtime";

interface EventStreamState {
  connected: boolean;
  latest: EngineEventEnvelope | null;
}

function websocketUrl(baseUrl: string, projectId: string): string {
  const url = new URL("/ws/events", baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("project_id", projectId);
  return url.toString();
}

export function useEngineEvents(projectId: string): EventStreamState {
  const [stream, setStream] = useState<EventStreamState>({
    connected: false,
    latest: null,
  });

  useEffect(() => {
    if (typeof WebSocket === "undefined") return;
    let active = true;
    let socket: WebSocket | null = null;
    void resolveEngineRuntime().then((runtime) => {
      if (!active || !runtime.base_url || !runtime.session_token) return;
      socket = new WebSocket(websocketUrl(runtime.base_url, projectId), [
        "story-engine.v1",
        `story-engine.token.${runtime.session_token}`,
      ]);
      socket.onopen = () =>
        setStream((current) => ({ ...current, connected: true }));
      socket.onmessage = (message) => {
        try {
          const event: unknown = JSON.parse(String(message.data));
          if (isEngineEvent(event) && event.project_id === projectId) {
            setStream({ connected: true, latest: event });
          }
        } catch {
          // A malformed event is ignored; HTTP state remains authoritative.
        }
      };
      socket.onclose = () =>
        setStream((current) => ({ ...current, connected: false }));
    });
    return () => {
      active = false;
      socket?.close();
    };
  }, [projectId]);

  return stream;
}
