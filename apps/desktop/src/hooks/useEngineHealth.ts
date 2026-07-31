import { useEffect, useState } from "react";

type EngineState = "checking" | "connected" | "offline";

interface EngineHealth {
  state: EngineState;
  version: string | null;
}

const engineUrl =
  import.meta.env.VITE_STORY_ENGINE_URL ?? "http://127.0.0.1:39281";

export function useEngineHealth(): EngineHealth {
  const [health, setHealth] = useState<EngineHealth>({
    state: "checking",
    version: null,
  });

  useEffect(() => {
    let active = true;
    const controller = new AbortController();

    async function checkHealth() {
      try {
        const response = await fetch(`${engineUrl}/health`, {
          signal: controller.signal,
        });
        if (!response.ok) {
          throw new Error(`Health check failed with ${response.status}`);
        }
        const payload = (await response.json()) as {
          status: string;
          version: string;
        };
        if (active) {
          setHealth({
            state: payload.status === "ok" ? "connected" : "offline",
            version: payload.version,
          });
        }
      } catch {
        if (active) {
          setHealth({ state: "offline", version: null });
        }
      }
    }

    void checkHealth();
    const interval = window.setInterval(checkHealth, 10_000);
    return () => {
      active = false;
      controller.abort();
      window.clearInterval(interval);
    };
  }, []);

  return health;
}

