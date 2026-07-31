import { resolveEngineRuntime } from "./runtime";

interface ErrorPayload {
  detail?: string;
}

export async function engineRequest<Response>(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const runtime = await resolveEngineRuntime();
  if (!runtime.base_url || !runtime.session_token) {
    throw new Error(runtime.last_error ?? "Story Engine is not running");
  }
  const response = await fetch(`${runtime.base_url}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${runtime.session_token}`,
      "Content-Type": "application/json",
      ...init.headers,
    },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as ErrorPayload;
    throw new Error(payload.detail ?? `Story Engine returned ${response.status}`);
  }
  return (await response.json()) as Response;
}
