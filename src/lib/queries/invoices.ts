/**
 * React Query bindings for Invoice Processing.
 *
 * The company id is part of every query key and every request, matching the
 * backend's URL-scoped isolation. Invalidations are scoped the same way.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  invoicesApi,
  toInvoice,
  toVendorMatch,
  type InvoiceLineItemDto,
  type InvoiceLineItemWrite,
  type InvoiceStatus,
  type InvoiceWriteDto,
  type ProcessResult,
  type UiInvoice,
  type VendorMatch,
} from "@/lib/api/invoices";
import { vendorKeys } from "@/lib/queries/vendors";
import { auditKeys } from "@/lib/queries/accountingAudit";

export const invoiceKeys = {
  all: ["invoices"] as const,
  list: (companyId: string) => ["invoices", companyId] as const,
  detail: (companyId: string, invoiceId: string) => ["invoices", companyId, invoiceId] as const,
  match: (companyId: string, invoiceId: string) =>
    ["invoices", companyId, invoiceId, "vendor-match"] as const,
  lineItems: (companyId: string, invoiceId: string) =>
    ["invoices", companyId, invoiceId, "line-items"] as const,
};

/**
 * TanStack Start renders on the server, where the backend is not reachable.
 * Fetching in the browser only avoids a hanging SSR pass and a hydration
 * mismatch; the data lands as soon as the component mounts.
 */
const isBrowser = typeof window !== "undefined";

export function useInvoices(
  companyId: string | undefined,
  filters: { status?: InvoiceStatus; documentId?: string } = {},
) {
  const { status, documentId } = filters;
  return useQuery<UiInvoice[]>({
    queryKey: [...invoiceKeys.list(companyId ?? "unknown"), { status, documentId }],
    queryFn: async () =>
      (await invoicesApi.list(companyId as string, { status, documentId })).map(toInvoice),
    enabled: isBrowser && Boolean(companyId),
  });
}

export function useInvoice(companyId: string | undefined, invoiceId: string | undefined) {
  return useQuery<UiInvoice>({
    queryKey: invoiceKeys.detail(companyId ?? "unknown", invoiceId ?? "unknown"),
    queryFn: async () => toInvoice(await invoicesApi.get(companyId as string, invoiceId as string)),
    enabled: isBrowser && Boolean(companyId) && Boolean(invoiceId),
  });
}

/**
 * Read-only match preview. It is enabled only when the caller actually needs
 * candidates (currently the ambiguous state), so the normal invoice list does
 * not multiply its requests.
 */
export function useVendorMatch(
  companyId: string | undefined,
  invoiceId: string | undefined,
  enabled: boolean,
) {
  return useQuery<VendorMatch>({
    queryKey: invoiceKeys.match(companyId ?? "unknown", invoiceId ?? "unknown"),
    queryFn: async () =>
      toVendorMatch(await invoicesApi.match(companyId as string, invoiceId as string)),
    enabled: isBrowser && Boolean(companyId) && Boolean(invoiceId) && enabled,
  });
}

export function useInvoiceLineItems(companyId: string | undefined, invoiceId: string | undefined) {
  return useQuery<InvoiceLineItemDto[]>({
    queryKey: invoiceKeys.lineItems(companyId ?? "unknown", invoiceId ?? "unknown"),
    queryFn: () => invoicesApi.lineItems(companyId as string, invoiceId as string),
    enabled: isBrowser && Boolean(companyId) && Boolean(invoiceId),
  });
}

/** Line-item writes refresh the item list and the invoice totals. */
function useInvalidateLineItems(companyId: string | undefined, invoiceId: string | undefined) {
  const queryClient = useQueryClient();
  return () => {
    if (!companyId || !invoiceId) return;
    queryClient.invalidateQueries({ queryKey: invoiceKeys.lineItems(companyId, invoiceId) });
    queryClient.invalidateQueries({ queryKey: invoiceKeys.list(companyId) });
  };
}

export function useCreateLineItem(companyId: string | undefined, invoiceId: string | undefined) {
  const invalidate = useInvalidateLineItems(companyId, invoiceId);
  return useMutation({
    mutationFn: (body: InvoiceLineItemWrite) =>
      invoicesApi.createLineItem(companyId as string, invoiceId as string, body),
    onSuccess: invalidate,
  });
}

export function useUpdateLineItem(companyId: string | undefined, invoiceId: string | undefined) {
  const invalidate = useInvalidateLineItems(companyId, invoiceId);
  return useMutation({
    mutationFn: (input: { id: string; body: InvoiceLineItemWrite }) =>
      invoicesApi.updateLineItem(companyId as string, invoiceId as string, input.id, input.body),
    onSuccess: invalidate,
  });
}

export function useDeleteLineItem(companyId: string | undefined, invoiceId: string | undefined) {
  const invalidate = useInvalidateLineItems(companyId, invoiceId);
  return useMutation({
    mutationFn: async (id: string) => {
      await invoicesApi.deleteLineItem(companyId as string, invoiceId as string, id);
      return id;
    },
    onSuccess: invalidate,
  });
}

/** Invalidation is scoped to the company whose invoices changed. */
function useInvalidateCompany(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return () => {
    if (!companyId) return;
    queryClient.invalidateQueries({ queryKey: invoiceKeys.list(companyId) });
    // Status, mapping and delete all record audit events; refresh activity.
    queryClient.invalidateQueries({ queryKey: auditKeys.list(companyId) });
  };
}

export function useCreateInvoice(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (body: InvoiceWriteDto) =>
      toInvoice(await invoicesApi.create(companyId as string, body)),
    onSuccess: invalidate,
  });
}

export function useUpdateInvoice(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (input: { id: string; body: InvoiceWriteDto }) =>
      toInvoice(await invoicesApi.update(companyId as string, input.id, input.body)),
    onSuccess: invalidate,
  });
}

export function useDeleteInvoice(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (id: string) => {
      await invoicesApi.remove(companyId as string, id);
      return id;
    },
    onSuccess: invalidate,
  });
}

export function useProcessInvoice(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      invoicesApi.process(companyId as string, id) as Promise<ProcessResult>,
    // The backend moves the invoice through PROCESSING to a final state and
    // may write extracted fields or auto-create a vendor, so both caches must
    // be refetched.
    onSuccess: () => {
      if (!companyId) return;
      queryClient.invalidateQueries({ queryKey: invoiceKeys.list(companyId) });
      queryClient.invalidateQueries({ queryKey: vendorKeys.list(companyId) });
    },
  });
}

export function useConfirmVendorMapping(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (input: { id: string; vendorId: string | null }) =>
      toInvoice(
        await invoicesApi.confirmVendorMapping(companyId as string, input.id, input.vendorId),
      ),
    onSuccess: invalidate,
  });
}
