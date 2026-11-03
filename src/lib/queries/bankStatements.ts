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
  type BankTransactionMappingWriteDto,
  type BankTransactionWriteDto,
  type IngestStatementResultDto,
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

/** Deterministic ledger name per transaction for a statement (table column). */
export function useBankStatementLedgerSuggestions(
  companyId: string | undefined,
  statementId: string | undefined,
) {
  return useQuery<Record<string, string | null>>({
    queryKey: [
      ...bankTransactionKeys.list(companyId ?? "unknown", statementId ?? "unknown"),
      "ledger-suggestions",
    ],
    queryFn: () =>
      bankStatementsApi.listLedgerSuggestions(companyId as string, statementId as string),
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

export function useUpdateBankTransactionMapping(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      statementId: string;
      transactionId: string;
      body: BankTransactionMappingWriteDto;
    }) =>
      toBankTransaction(
        await bankStatementsApi.updateTransactionMapping(
          companyId as string,
          input.statementId,
          input.transactionId,
          input.body,
        ),
      ),
    onSuccess: (_updated, input) => {
      if (!companyId) return;
      queryClient.invalidateQueries({
        queryKey: bankTransactionKeys.list(companyId, input.statementId),
      });
      queryClient.invalidateQueries({ queryKey: ["vouchers", companyId] });
    },
  });
}

export function useUpdateBankTransaction(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (input: {
      statementId: string;
      transactionId: string;
      body: Partial<BankTransactionWriteDto>;
    }) =>
      toBankTransaction(
        await bankStatementsApi.updateTransaction(
          companyId as string,
          input.statementId,
          input.transactionId,
          input.body,
        ),
      ),
    onSuccess: (_updated, input) => {
      if (!companyId) return;
      queryClient.invalidateQueries({
        queryKey: bankTransactionKeys.list(companyId, input.statementId),
      });
    },
  });
}

export function useProcessBankStatement(companyId: string | undefined) {
  return useMutation({
    mutationFn: async (id: string) =>
      bankStatementsApi.process(companyId as string, id) as Promise<ProcessStatementResultDto>,
  });
}

export interface BankBatchItemResult {
  statementId: string;
  status: BankStatementStatus;
  configured: boolean;
  message: string;
}

export interface BankBatchProgress {
  done: number;
  total: number;
  /** The statement just completed, or null before the first finishes. */
  last: BankBatchItemResult | null;
}

/**
 * Extract a queue of statements one at a time, the same way the Document Inbox
 * extracts invoices: a shared model pass per statement keeps per-statement
 * accuracy up, and the caller gets live progress through `onProgress`.
 */
export function useProcessBankStatementsBatch(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      statementIds,
      onProgress,
    }: {
      statementIds: string[];
      onProgress?: (p: BankBatchProgress) => void;
    }): Promise<BankBatchItemResult[]> => {
      const results: BankBatchItemResult[] = [];
      const total = statementIds.length;
      onProgress?.({ done: 0, total, last: null });
      for (const statementId of statementIds) {
        let item: BankBatchItemResult;
        try {
          const r = await bankStatementsApi.process(companyId as string, statementId);
          item = { statementId, status: r.status, configured: r.configured, message: r.message };
        } catch (e) {
          // One statement failing must not abort the queue.
          item = {
            statementId,
            status: "FAILED",
            configured: true,
            message: e instanceof Error ? e.message : "Extraction failed",
          };
        }
        results.push(item);
        onProgress?.({ done: results.length, total, last: item });
      }
      return results;
    },
    onSuccess: () => {
      if (!companyId) return;
      queryClient.invalidateQueries({ queryKey: bankStatementKeys.list(companyId) });
      queryClient.invalidateQueries({ queryKey: bankTransactionKeys.all });
    },
  });
}

export interface BankIngestItemResult {
  documentId: string;
  statementId: string | null;
  status: BankStatementStatus | null;
  configured: boolean;
  reason: string | null;
  message: string;
}

export interface BankIngestProgress {
  done: number;
  total: number;
  last: BankIngestItemResult | null;
}

/**
 * Direct-upload ingest queue: one document at a time, each read, matched to a
 * bank account and turned into a statement server-side. The caller selects no
 * account. Live progress is reported through `onProgress`.
 */
export function useIngestBankStatementsBatch(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      documentIds,
      onProgress,
    }: {
      documentIds: string[];
      onProgress?: (p: BankIngestProgress) => void;
    }): Promise<BankIngestItemResult[]> => {
      const results: BankIngestItemResult[] = [];
      const total = documentIds.length;
      onProgress?.({ done: 0, total, last: null });
      for (const documentId of documentIds) {
        let item: BankIngestItemResult;
        try {
          const r: IngestStatementResultDto = await bankStatementsApi.ingestDocument(
            companyId as string,
            documentId,
          );
          item = {
            documentId,
            statementId: r.statement_id,
            status: r.status,
            configured: r.configured,
            reason: r.reason,
            message: r.message,
          };
        } catch (e) {
          item = {
            documentId,
            statementId: null,
            status: null,
            configured: true,
            reason: "INGEST_FAILED",
            message: e instanceof Error ? e.message : "Could not ingest the file",
          };
        }
        results.push(item);
        onProgress?.({ done: results.length, total, last: item });
      }
      return results;
    },
    onSuccess: () => {
      if (!companyId) return;
      queryClient.invalidateQueries({ queryKey: bankStatementKeys.list(companyId) });
      queryClient.invalidateQueries({ queryKey: bankTransactionKeys.all });
    },
  });
}
