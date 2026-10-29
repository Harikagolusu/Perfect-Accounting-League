/**
 * React Query bindings for draft vouchers, approval, export and audit.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  toAuditEvent,
  toLedgerSuggestion,
  toVoucher,
  vouchersApi,
  type Voucher,
  type VoucherActionDto,
  type VoucherListFilters,
  type VoucherLineWriteDto,
  type VoucherPostDto,
  type VoucherUpdateDto,
  type VoucherWriteDto,
} from "@/lib/api/vouchers";
import type { DownloadedFile } from "@/lib/api/client";

export const voucherKeys = {
  all: ["vouchers"] as const,
  list: (companyId: string, filters: VoucherListFilters = {}) =>
    ["vouchers", companyId, filters] as const,
  detail: (companyId: string, voucherId: string) =>
    ["vouchers", companyId, "detail", voucherId] as const,
  audit: (companyId: string, voucherId: string) =>
    ["vouchers", companyId, "audit", voucherId] as const,
  invoiceSuggestion: (companyId: string, invoiceId: string) =>
    ["vouchers", companyId, "suggestion", "invoice", invoiceId] as const,
  bankSuggestion: (companyId: string, statementId: string, transactionId: string) =>
    ["vouchers", companyId, "suggestion", "bank", statementId, transactionId] as const,
  exportPreview: (companyId: string, dateFrom?: string, dateTo?: string) =>
    ["vouchers", companyId, "export-preview", dateFrom ?? "", dateTo ?? ""] as const,
};

const isBrowser = typeof window !== "undefined";

export function useVouchers(
  companyId: string | undefined,
  filters: VoucherListFilters = {},
  enabled = true,
) {
  return useQuery<Voucher[]>({
    queryKey: voucherKeys.list(companyId ?? "unknown", filters),
    queryFn: async () => (await vouchersApi.list(companyId as string, filters)).map(toVoucher),
    enabled: isBrowser && Boolean(companyId) && enabled,
  });
}

export function useVoucher(companyId: string | undefined, voucherId: string | undefined) {
  return useQuery<Voucher>({
    queryKey: voucherKeys.detail(companyId ?? "unknown", voucherId ?? "unknown"),
    queryFn: async () => toVoucher(await vouchersApi.get(companyId as string, voucherId as string)),
    enabled: isBrowser && Boolean(companyId) && Boolean(voucherId),
  });
}

export function useVoucherAudit(
  companyId: string | undefined,
  voucherId: string | undefined,
  enabled = true,
) {
  return useQuery({
    queryKey: voucherKeys.audit(companyId ?? "unknown", voucherId ?? "unknown"),
    queryFn: async () =>
      (await vouchersApi.audit(companyId as string, voucherId as string)).map(toAuditEvent),
    enabled: isBrowser && Boolean(companyId) && Boolean(voucherId) && enabled,
  });
}

export function useInvoiceLedgerSuggestion(
  companyId: string | undefined,
  invoiceId: string | undefined,
) {
  return useQuery({
    queryKey: voucherKeys.invoiceSuggestion(companyId ?? "unknown", invoiceId ?? "unknown"),
    queryFn: async () =>
      (
        await vouchersApi.invoiceSuggestion(companyId as string, invoiceId as string)
      ).suggestions.map(toLedgerSuggestion),
    enabled: isBrowser && Boolean(companyId) && Boolean(invoiceId),
  });
}

export function useBankLedgerSuggestion(
  companyId: string | undefined,
  statementId: string | undefined,
  transactionId: string | undefined,
) {
  return useQuery({
    queryKey: voucherKeys.bankSuggestion(
      companyId ?? "unknown",
      statementId ?? "unknown",
      transactionId ?? "unknown",
    ),
    queryFn: async () =>
      (
        await vouchersApi.bankSuggestion(
          companyId as string,
          statementId as string,
          transactionId as string,
        )
      ).suggestions.map(toLedgerSuggestion),
    enabled: isBrowser && Boolean(companyId) && Boolean(statementId) && Boolean(transactionId),
  });
}

export function useVoucherExportPreview(
  companyId: string | undefined,
  dateFrom?: string,
  dateTo?: string,
  postingMonth?: string,
) {
  return useQuery({
    queryKey: [
      ...voucherKeys.exportPreview(companyId ?? "unknown", dateFrom, dateTo),
      postingMonth,
    ],
    queryFn: async () =>
      vouchersApi.exportPreview(companyId as string, { dateFrom, dateTo, postingMonth }),
    enabled: isBrowser && Boolean(companyId),
  });
}

function useInvalidateVouchers(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return () => {
    if (companyId) queryClient.invalidateQueries({ queryKey: voucherKeys.all });
  };
}

export function useCreateVoucher(companyId: string | undefined) {
  const invalidate = useInvalidateVouchers(companyId);
  return useMutation({
    mutationFn: async (body: VoucherWriteDto) =>
      toVoucher(await vouchersApi.create(companyId as string, body)),
    onSuccess: invalidate,
  });
}

export function useUpdateVoucher(companyId: string | undefined) {
  const invalidate = useInvalidateVouchers(companyId);
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: VoucherUpdateDto }) =>
      toVoucher(await vouchersApi.update(companyId as string, id, body)),
    onSuccess: invalidate,
  });
}

export function useDeleteVoucher(companyId: string | undefined) {
  const invalidate = useInvalidateVouchers(companyId);
  return useMutation({
    mutationFn: async (id: string) => vouchersApi.remove(companyId as string, id),
    onSuccess: invalidate,
  });
}

export function useCreateVoucherFromInvoice(companyId: string | undefined) {
  const invalidate = useInvalidateVouchers(companyId);
  return useMutation({
    mutationFn: async ({ invoiceId, actor }: { invoiceId: string; actor?: string }) =>
      toVoucher(await vouchersApi.fromInvoice(companyId as string, invoiceId, actor)),
    onSuccess: invalidate,
  });
}

export function useCreateVoucherFromBank(companyId: string | undefined) {
  const invalidate = useInvalidateVouchers(companyId);
  return useMutation({
    mutationFn: async ({
      transactionId,
      splitLines,
      actor,
    }: {
      transactionId: string;
      splitLines?: VoucherLineWriteDto[];
      actor?: string;
    }) =>
      toVoucher(
        await vouchersApi.fromBank(companyId as string, transactionId, {
          splitLines,
          actor,
        }),
      ),
    onSuccess: invalidate,
  });
}

export function useVoucherAction(
  companyId: string | undefined,
  action: "submit" | "approve" | "reject" | "reopen",
) {
  const invalidate = useInvalidateVouchers(companyId);
  return useMutation({
    mutationFn: async ({ id, body = {} }: { id: string; body?: VoucherActionDto }) => {
      const call = vouchersApi[action];
      return toVoucher(await call(companyId as string, id, body));
    },
    onSuccess: invalidate,
  });
}

export function useVerifyVoucherPosting(companyId: string | undefined) {
  const invalidate = useInvalidateVouchers(companyId);
  return useMutation({
    mutationFn: async ({ id, body }: { id: string; body: VoucherActionDto }) =>
      toVoucher(await vouchersApi.verifyPosting(companyId as string, id, body)),
    onSuccess: invalidate,
  });
}

export function useExportVoucher(companyId: string | undefined) {
  const invalidate = useInvalidateVouchers(companyId);
  return useMutation<DownloadedFile, Error, { id: string; actor?: string; postingMonth?: string }>({
    mutationFn: async ({ id, actor, postingMonth }) =>
      vouchersApi.exportOne(companyId as string, id, { actor, posting_month: postingMonth }),
    onSuccess: invalidate,
  });
}

/** Post one approved voucher straight into TallyPrime. No file is produced. */
export function usePostVoucherToTally(companyId: string | undefined) {
  const invalidate = useInvalidateVouchers(companyId);
  return useMutation<VoucherPostDto, Error, { id: string; actor?: string; postingMonth?: string }>({
    mutationFn: async ({ id, actor, postingMonth }) =>
      vouchersApi.postToTally(companyId as string, id, { actor, posting_month: postingMonth }),
    onSuccess: invalidate,
  });
}

export function useExportVouchers(companyId: string | undefined) {
  const invalidate = useInvalidateVouchers(companyId);
  return useMutation<
    DownloadedFile,
    Error,
    {
      voucherIds?: string[];
      dateFrom?: string;
      dateTo?: string;
      postingMonth?: string;
      actor?: string;
    }
  >({
    mutationFn: async (body) => vouchersApi.exportFile(companyId as string, body),
    onSuccess: invalidate,
  });
}
