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

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        "X-Demo-Role": sessionStorage.getItem("cdi-role") || "viewer",
        "X-Demo-Passcode": sessionStorage.getItem("cdi-passcode") || "",
        ...(init?.headers ?? {}),
      },
    });
  } catch {
    throw new ApiError("The planning API is not responding.", 0);
  }
  const text = await response.text();
  const body = text ? readJson(text) : {};
  if (!response.ok) {
    const detail = body.detail;
    const reason = typeof detail === "string" ? detail : `Request failed (${response.status}).`;
    if (response.status === 401) throw new ApiError(`Sign-in failed (401). ${reason}`, 401);
    if (response.status === 403) throw new ApiError(`This role cannot do that (403). ${reason}`, 403);
    throw new ApiError(reason, response.status);
  }
  return body as T;
}
