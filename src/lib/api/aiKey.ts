/**
 * DeepSeek API key saved from Settings.
 *
 * The backend verifies a pasted key with DeepSeek, stores it encrypted and only
 * ever returns a masked form, so the browser never holds the saved key.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

export interface AiKeyStatusDto {
  configured: boolean;
  /** "settings" = saved here; "environment" = server .env fallback. */
  source: "settings" | "environment" | null;
  masked: string | null;
}

const url = `${API_V1_PREFIX}/ai/key`;

export const aiKeyApi = {
  get: () => apiFetch<AiKeyStatusDto>(url),
  save: (apiKey: string) =>
    apiFetch<AiKeyStatusDto>(url, { method: "PUT", body: JSON.stringify({ api_key: apiKey }) }),
  remove: () => apiFetch<AiKeyStatusDto>(url, { method: "DELETE" }),
};
