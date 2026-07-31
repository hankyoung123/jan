const engineUrl =
  import.meta.env.VITE_STORY_ENGINE_URL ?? "http://127.0.0.1:39281";
const sessionToken =
  import.meta.env.VITE_STORY_ENGINE_TOKEN ?? "development-token";

interface ErrorPayload {
  detail?: string;
}

export async function engineRequest<Response>(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const response = await fetch(`${engineUrl}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${sessionToken}`,
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
