/**
 * React Query bindings for Bank Accounts.
 *
 * The company id is part of every query key and every request, matching the
 * backend's URL-scoped isolation. Invalidations are scoped the same way so a
 * write in one company never refetches another company's accounts.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  bankAccountsApi,
  toBankAccount,
  type BankAccountWriteDto,
  type UiBankAccount,
} from "@/lib/api/bankAccounts";

export const bankAccountKeys = {
  all: ["bankAccounts"] as const,
  list: (companyId: string) => ["bankAccounts", companyId] as const,
  detail: (companyId: string, accountId: string) => ["bankAccounts", companyId, accountId] as const,
};

/**
 * TanStack Start renders on the server, where the backend is not reachable.
 * Fetching in the browser only avoids a hanging SSR pass and a hydration
 * mismatch; the data lands as soon as the component mounts.
 */
const isBrowser = typeof window !== "undefined";

export function useBankAccounts(companyId: string | undefined) {
  return useQuery<UiBankAccount[]>({
    queryKey: bankAccountKeys.list(companyId ?? "unknown"),
    queryFn: async () => (await bankAccountsApi.list(companyId as string)).map(toBankAccount),
    enabled: isBrowser && Boolean(companyId),
  });
}

/** Invalidation is scoped to the company whose accounts changed. */
function useInvalidateCompany(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return () => {
    if (companyId) queryClient.invalidateQueries({ queryKey: bankAccountKeys.list(companyId) });
  };
}

export function useCreateBankAccount(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (body: BankAccountWriteDto) =>
      toBankAccount(await bankAccountsApi.create(companyId as string, body)),
    onSuccess: invalidate,
  });
}

export function useUpdateBankAccount(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (input: { id: string; body: BankAccountWriteDto }) =>
      toBankAccount(await bankAccountsApi.update(companyId as string, input.id, input.body)),
    onSuccess: invalidate,
  });
}

export function useDeactivateBankAccount(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (id: string) =>
      toBankAccount(await bankAccountsApi.deactivate(companyId as string, id)),
    onSuccess: invalidate,
  });
}
