const API_BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");

function readJson(text: string): { detail?: unknown } {
  try {
    return JSON.parse(text) as { detail?: unknown };
  } catch {
    throw new Error(
      "The planning API did not answer with data. The host returned a web page instead. On Vercel, set Root Directory to the repository root (not frontend) and redeploy so /api runs the Python service.",
    );
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const text = await response.text();
  const body = text ? readJson(text) : {};
  if (!response.ok) {
    const detail = body.detail;
    const message = typeof detail === "string" ? detail : `Request failed (${response.status}).`;
    throw new Error(message);
  }
  return body as T;
}
