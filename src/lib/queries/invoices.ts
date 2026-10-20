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
  type InvoiceStatus,
  type InvoiceWriteDto,
  type ProcessResult,
  type UiInvoice,
} from "@/lib/api/invoices";

export const invoiceKeys = {
  all: ["invoices"] as const,
  list: (companyId: string) => ["invoices", companyId] as const,
  detail: (companyId: string, invoiceId: string) => ["invoices", companyId, invoiceId] as const,
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

/** Invalidation is scoped to the company whose invoices changed. */
function useInvalidateCompany(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return () => {
    if (companyId) queryClient.invalidateQueries({ queryKey: invoiceKeys.list(companyId) });
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
    // may write extracted fields, so the cached list must be refetched.
    onSuccess: () => {
      if (companyId) queryClient.invalidateQueries({ queryKey: invoiceKeys.list(companyId) });
    },
  });
}
