export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const text = await response.text();
  const body = text ? JSON.parse(text) : {};
  if (!response.ok) {
    const detail = body?.detail;
    const message = typeof detail === "string" ? detail : `Request failed (${response.status}).`;
    throw new Error(message);
  }
  return body as T;
}
