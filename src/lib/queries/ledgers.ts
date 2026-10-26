/**
 * React Query bindings for the company-scoped Ledger Master.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ledgersApi, toLedger, toLedgerPayload, type Ledger } from "@/lib/api/ledgers";

export const ledgerKeys = {
  all: ["ledgers"] as const,
  list: (companyId: string, includeInactive: boolean) =>
    ["ledgers", companyId, includeInactive] as const,
};

const isBrowser = typeof window !== "undefined";

export function useLedgers(companyId: string | undefined, includeInactive = false) {
  return useQuery<Ledger[]>({
    queryKey: ledgerKeys.list(companyId ?? "unknown", includeInactive),
    queryFn: async () =>
      (await ledgersApi.list(companyId as string, includeInactive)).map(toLedger),
    enabled: isBrowser && Boolean(companyId),
  });
}

function useInvalidate(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return () => {
    if (companyId) queryClient.invalidateQueries({ queryKey: ledgerKeys.all });
  };
}

export function useCreateLedger(companyId: string | undefined) {
  const invalidate = useInvalidate(companyId);
  return useMutation({
    mutationFn: async (ledger: Partial<Ledger>) =>
      toLedger(await ledgersApi.create(companyId as string, toLedgerPayload(ledger))),
    onSuccess: invalidate,
  });
}

export function useUpdateLedger(companyId: string | undefined) {
  const invalidate = useInvalidate(companyId);
  return useMutation({
    mutationFn: async ({ id, changes }: { id: string; changes: Partial<Ledger> }) =>
      toLedger(await ledgersApi.update(companyId as string, id, toLedgerPayload(changes))),
    onSuccess: invalidate,
  });
}

export function useDeactivateLedger(companyId: string | undefined) {
  const invalidate = useInvalidate(companyId);
  return useMutation({
    mutationFn: async (id: string) =>
      toLedger(await ledgersApi.deactivate(companyId as string, id)),
    onSuccess: invalidate,
  });
}
