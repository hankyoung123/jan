import { useEffect, useState } from "react";

import {
  resolveEngineRuntime,
  restartEngineRuntime,
  subscribeEngineRuntime,
} from "../features/api/runtime";

type EngineState = "checking" | "connected" | "offline" | "restarting";

interface EngineHealthState {
  state: EngineState;
  version: string | null;
}

interface EngineHealth extends EngineHealthState {
  restart: () => Promise<void>;
}

export function useEngineHealth(): EngineHealth {
  const [health, setHealth] = useState<EngineHealthState>({
    state: "checking",
    version: null,
  });

  async function checkHealth(signal?: AbortSignal) {
    try {
      const runtime = await resolveEngineRuntime();
      if (
        !runtime.base_url ||
        runtime.phase === "crashed" ||
        runtime.phase === "stopped"
      ) {
        throw new Error(runtime.last_error ?? "Story Engine is offline");
      }
      const response = await fetch(`${runtime.base_url}/health`, { signal });
      if (!response.ok) {
        throw new Error(`Health check failed with ${response.status}`);
      }
      const payload = (await response.json()) as {
        status: string;
        version: string;
      };
      setHealth({
        state: payload.status === "ok" ? "connected" : "offline",
        version: payload.version,
      });
    } catch {
      if (!signal?.aborted) {
        setHealth({ state: "offline", version: null });
      }
    }
  }

  async function restart() {
    setHealth({ state: "restarting", version: null });
    try {
      await restartEngineRuntime();
      await checkHealth();
    } catch {
      setHealth({ state: "offline", version: null });
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    let disposed = false;
    let unlisten: () => void = () => undefined;
    void checkHealth(controller.signal);
    void subscribeEngineRuntime((runtime) => {
      if (runtime.phase === "ready") {
        void checkHealth(controller.signal);
      } else if (runtime.phase === "crashed" || runtime.phase === "stopped") {
        setHealth({ state: "offline", version: null });
      } else {
        setHealth({ state: "checking", version: null });
      }
    }).then((cleanup) => {
      if (disposed) cleanup();
      else unlisten = cleanup;
    });
    const interval = window.setInterval(
      () => void checkHealth(controller.signal),
      10_000,
    );
    return () => {
      disposed = true;
      controller.abort();
      unlisten();
      window.clearInterval(interval);
    };
  }, []);

  return { ...health, restart };
}
