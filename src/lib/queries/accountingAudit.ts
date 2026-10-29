/**
 * React Query binding for the persisted company-wide audit trail.
 *
 * Components read this instead of a browser-only array, so the Activity and
 * Audit views reload yesterday's work from PostgreSQL on every mount.
 */

import { useQuery } from "@tanstack/react-query";

import { accountingAuditApi, toAuditEvent } from "@/lib/api/accountingAudit";
import type { AuditEvent } from "@/lib/types";

const isBrowser = typeof window !== "undefined";

export const auditKeys = {
  all: ["accounting-audit"] as const,
  list: (companyId: string) => ["accounting-audit", companyId] as const,
};

export function useAccountingAudit(companyId: string | undefined) {
  return useQuery<AuditEvent[]>({
    queryKey: auditKeys.list(companyId ?? "unknown"),
    queryFn: async () => (await accountingAuditApi.list(companyId as string)).map(toAuditEvent),
    enabled: isBrowser && Boolean(companyId),
  });
}
