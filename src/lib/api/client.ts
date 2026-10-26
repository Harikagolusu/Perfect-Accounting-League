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
  if (detail && typeof detail === "object") {
    const message = (detail as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) return message;
  }
  return `Request failed (${status})`;
}

/**
 * Extract a filename from `Content-Disposition`.
 *
 * The backend emits the simple quoted form, but supporting `filename*` keeps
 * this correct if a future export has a non-ASCII company name.
 */
export function parseContentDisposition(header: string | null): string | null {
  if (!header) return null;
  const encoded = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(header);
  if (encoded?.[1]) {
    try {
      return decodeURIComponent(encoded[1].trim().replace(/^"|"$/g, ""));
    } catch {
      return encoded[1].trim().replace(/^"|"$/g, "");
    }
  }
  const quoted = /filename\s*=\s*"([^"]+)"/i.exec(header);
  if (quoted?.[1]) return quoted[1];
  const bare = /filename\s*=\s*([^;]+)/i.exec(header);
  return bare?.[1]?.trim() ?? null;
}

export type DownloadedFile = {
  blob: Blob;
  filename: string;
};

/**
 * Download a binary response without parsing it as JSON.
 *
 * `apiFetch` is JSON-only by design, so XLSX downloads use this companion. It
 * keeps the same base URL, query encoding and error shape.
 */
export async function apiDownload(
  path: string,
  init: RequestInit = {},
  params?: QueryParams,
): Promise<DownloadedFile> {
  const headers: Record<string, string> = {
    Accept: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ...(init.headers as Record<string, string> | undefined),
  };
  const response = await fetch(buildUrl(path, params), { ...init, headers });

  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiError(
      response.status,
      detailToMessage(response.status, (payload as { detail?: unknown } | null)?.detail),
    );
  }

  return {
    blob: await response.blob(),
    filename:
      parseContentDisposition(response.headers.get("content-disposition")) ?? "download.xlsx",
  };
}

export async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  params?: QueryParams,
): Promise<T> {
  // FormData keeps its own Content-Type so the browser can add the multipart
  // boundary; setting one by hand produces an unparseable body.
  const isFormData = typeof FormData !== "undefined" && init.body instanceof FormData;
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...(init.body && !isFormData ? { "Content-Type": "application/json" } : {}),
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

  // 204 No Content, used by deletes.
  if (response.status === 204) return undefined as T;

  return payload as T;
}
