/**
 * Minimal typed fetch wrapper for the PAL backend.
 *
 * Every backend call funnels through `apiFetch`, so the base URL, JSON
 * handling and error shape are defined in exactly one place instead of being
 * repeated across features.
 */

/** Backend origin. Override with VITE_API_BASE_URL (see .env.local). */
export const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

/** Versioned route prefix; mirrors `API_V1_PREFIX` in the backend settings. */
export const API_V1_PREFIX = "/api/v1";

export type QueryParams = Record<string, string | number | boolean | undefined>;

/** An error carrying the HTTP status and the backend's human-readable detail. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

function buildUrl(path: string, params?: QueryParams): string {
  const url = `${API_BASE_URL}${path}`;
  if (!params) return url;

  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `${url}?${query}` : url;
}

/** Turns FastAPI's `detail` (string or validation-error list) into one line. */
function detailToMessage(status: number, detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: string } | undefined;
    if (first?.msg) return first.msg;
  }
  return `Request failed (${status})`;
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  params?: QueryParams,
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.body ? { "Content-Type": "application/json" } : {}),
    ...(init.headers as Record<string, string> | undefined),
  };

  const response = await fetch(buildUrl(path, params), { ...init, headers });
  const payload = await response.json().catch(() => null);

  if (!response.ok) {
    throw new ApiError(
      response.status,
      detailToMessage(response.status, (payload as { detail?: unknown } | null)?.detail),
    );
  }

  return payload as T;
}
