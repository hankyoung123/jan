import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export type EnginePhase = "starting" | "ready" | "stopped" | "crashed";

export interface EngineRuntimeState {
  phase: EnginePhase;
  base_url: string | null;
  websocket_url: string | null;
  session_token: string | null;
  restart_count: number;
  last_error: string | null;
}

export type EngineRuntimeStatus = Omit<EngineRuntimeState, "session_token">;

const developmentRuntime: EngineRuntimeState = {
  phase: "ready",
  base_url:
    import.meta.env.VITE_STORY_ENGINE_URL ?? "http://127.0.0.1:39281",
  websocket_url: null,
  session_token:
    import.meta.env.VITE_STORY_ENGINE_TOKEN ?? "development-token",
  restart_count: 0,
  last_error: null,
};

export async function resolveEngineRuntime(): Promise<EngineRuntimeState> {
  if (!isTauri()) return developmentRuntime;
  return invoke<EngineRuntimeState>("engine_runtime_state");
}

export async function restartEngineRuntime(): Promise<EngineRuntimeState> {
  if (!isTauri()) return developmentRuntime;
  return invoke<EngineRuntimeState>("restart_story_engine");
}

export async function subscribeEngineRuntime(
  listener: (state: EngineRuntimeStatus) => void,
): Promise<UnlistenFn> {
  if (!isTauri()) return () => undefined;
  return listen<EngineRuntimeStatus>("story-engine://status", (event) => {
    listener(event.payload);
  });
}
