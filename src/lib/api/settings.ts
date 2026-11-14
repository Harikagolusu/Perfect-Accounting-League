/**
 * Company settings API.
 *
 * The whole Settings page persists as one JSON document per company: the field
 * sections (`fields`), the user list (`users`) and the role/permission matrix
 * (`roles`). The backend validates only the envelope (object, bounded size), so
 * the document can grow without any backend change.
 *
 * Enforcement note: `users`/`roles` are configuration only. PAL has no auth
 * layer yet, so these are stored and restored but not yet enforced at runtime.
 */

import { API_V1_PREFIX, apiFetch } from "@/lib/api/client";

export type SettingsFields = Record<string, string | boolean>;

export interface SettingsUser {
  name: string;
  role: string;
  active: boolean;
}

export interface SettingsDoc {
  fields: SettingsFields;
  users: SettingsUser[];
  /** Role name -> one boolean per permission column. */
  roles: Record<string, boolean[]>;
}

export interface CompanySettingsDto {
  company_id: string;
  /** Partial: an unsaved company returns {}, and older rows may lack keys. */
  data: Partial<SettingsDoc>;
  updated_at: string | null;
}

export const settingsApi = {
  get: (companyId: string) =>
    apiFetch<CompanySettingsDto>(`${API_V1_PREFIX}/companies/${companyId}/settings`),

  save: (companyId: string, data: SettingsDoc) =>
    apiFetch<CompanySettingsDto>(`${API_V1_PREFIX}/companies/${companyId}/settings`, {
      method: "PUT",
      body: JSON.stringify({ data }),
    }),
};
