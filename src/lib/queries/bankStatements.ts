/**
 * React Query bindings for Bank Statements and their transactions.
 *
 * The company id is part of every query key and every request, matching the
 * backend's URL-scoped isolation. A transaction is additionally keyed by the
 * statement that owns it, so one statement's rows are never cached under
 * another's.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  bankStatementsApi,
  toBankStatement,
  toBankTransaction,
  type BankStatementStatus,
  type BankStatementWriteDto,
  type BankTransactionWriteDto,
  type ProcessStatementResultDto,
  type UiBankStatement,
  type UiBankTransaction,
} from "@/lib/api/bankStatements";

export const bankStatementKeys = {
  all: ["bankStatements"] as const,
  list: (companyId: string) => ["bankStatements", companyId] as const,
  detail: (companyId: string, statementId: string) =>
    ["bankStatements", companyId, statementId] as const,
};

export const bankTransactionKeys = {
  all: ["bankTransactions"] as const,
  list: (companyId: string, statementId: string) =>
    ["bankTransactions", companyId, statementId] as const,
};

/**
 * TanStack Start renders on the server, where the backend is not reachable.
 * Fetching in the browser only avoids a hanging SSR pass and a hydration
 * mismatch; the data lands as soon as the component mounts.
 */
const isBrowser = typeof window !== "undefined";

export function useBankStatements(
  companyId: string | undefined,
  filters: { bankAccountId?: string; status?: BankStatementStatus } = {},
) {
  const { bankAccountId, status } = filters;
  return useQuery<UiBankStatement[]>({
    queryKey: [...bankStatementKeys.list(companyId ?? "unknown"), { bankAccountId, status }],
    queryFn: async () =>
      (await bankStatementsApi.list(companyId as string, { bankAccountId, status })).map(
        toBankStatement,
      ),
    enabled: isBrowser && Boolean(companyId),
  });
}

export function useBankStatement(companyId: string | undefined, statementId: string | undefined) {
  return useQuery<UiBankStatement>({
    queryKey: bankStatementKeys.detail(companyId ?? "unknown", statementId ?? "unknown"),
    queryFn: async () =>
      toBankStatement(await bankStatementsApi.get(companyId as string, statementId as string)),
    enabled: isBrowser && Boolean(companyId) && Boolean(statementId),
  });
}

export function useBankTransactions(
  companyId: string | undefined,
  statementId: string | undefined,
  filters: { dateFrom?: string; dateTo?: string } = {},
) {
  const { dateFrom, dateTo } = filters;
  return useQuery<UiBankTransaction[]>({
    queryKey: [
      ...bankTransactionKeys.list(companyId ?? "unknown", statementId ?? "unknown"),
      { dateFrom, dateTo },
    ],
    queryFn: async () =>
      (
        await bankStatementsApi.listTransactions(companyId as string, statementId as string, {
          dateFrom,
          dateTo,
        })
      ).map(toBankTransaction),
    enabled: isBrowser && Boolean(companyId) && Boolean(statementId),
  });
}

/**
 * Invalidations are scoped to the company whose data changed, and transactions
 * additionally to the statement that owns them.
 */
function useInvalidateStatementScope(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return (statementId?: string) => {
    if (!companyId) return;
    queryClient.invalidateQueries({ queryKey: bankStatementKeys.list(companyId) });
    if (statementId) {
      queryClient.invalidateQueries({
        queryKey: bankStatementKeys.detail(companyId, statementId),
      });
      queryClient.invalidateQueries({
        queryKey: bankTransactionKeys.list(companyId, statementId),
      });
    }
  };
}

export function useCreateBankStatement(companyId: string | undefined) {
  const invalidate = useInvalidateStatementScope(companyId);
  return useMutation({
    mutationFn: async (body: BankStatementWriteDto) =>
      toBankStatement(await bankStatementsApi.create(companyId as string, body)),
    onSuccess: (created) => invalidate(created.id),
  });
}

export function useUpdateBankStatement(companyId: string | undefined) {
  const invalidate = useInvalidateStatementScope(companyId);
  return useMutation({
    mutationFn: async (input: { id: string; body: BankStatementWriteDto }) =>
      toBankStatement(await bankStatementsApi.update(companyId as string, input.id, input.body)),
    onSuccess: (updated) => invalidate(updated.id),
  });
}

export function useDeleteBankStatement(companyId: string | undefined) {
  const invalidate = useInvalidateStatementScope(companyId);
  return useMutation({
    mutationFn: async (id: string) => {
      await bankStatementsApi.remove(companyId as string, id);
      return id;
    },
    onSuccess: invalidate,
  });
}

export function useCreateBankTransaction(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: { statementId: string; body: BankTransactionWriteDto }) =>
      toBankTransaction(
        await bankStatementsApi.createTransaction(
          companyId as string,
          input.statementId,
          input.body,
        ),
      ),
    // Only this statement's rows change, so only its list is invalidated.
    onSuccess: (_created, input) => {
      if (companyId) {
        queryClient.invalidateQueries({
          queryKey: bankTransactionKeys.list(companyId, input.statementId),
        });
      }
    },
  });
}

export function useProcessBankStatement(companyId: string | undefined) {
  return useMutation({
    mutationFn: async (id: string) =>
      bankStatementsApi.process(companyId as string, id) as Promise<ProcessStatementResultDto>,
  });
}
