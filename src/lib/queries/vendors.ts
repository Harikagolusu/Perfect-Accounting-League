/**
 * React Query bindings for the Vendor Master.
 *
 * The company id is part of every query key and every request. That is what
 * keeps one company's vendors out of another's cache, and it matches the
 * backend's URL-scoped isolation exactly.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { toVendor, toVendorPayload, vendorsApi } from "@/lib/api/vendors";
import { auditKeys } from "@/lib/queries/accountingAudit";
import type { Vendor } from "@/lib/types";

export const vendorKeys = {
  all: ["vendors"] as const,
  list: (companyId: string) => ["vendors", companyId] as const,
  detail: (companyId: string, vendorId: string) => ["vendors", companyId, vendorId] as const,
};

/**
 * TanStack Start renders on the server, where the backend is not reachable.
 * Fetching in the browser only avoids a hanging SSR pass and a hydration
 * mismatch; the data lands as soon as the component mounts.
 */
const isBrowser = typeof window !== "undefined";

export function useVendors(companyId: string | undefined, includeInactive = false) {
  return useQuery<Vendor[]>({
    queryKey: vendorKeys.list(companyId ?? "unknown"),
    queryFn: async () =>
      (await vendorsApi.list(companyId as string, includeInactive)).map(toVendor),
    enabled: isBrowser && Boolean(companyId),
  });
}

export function useVendor(companyId: string | undefined, vendorId: string | undefined) {
  return useQuery<Vendor>({
    queryKey: vendorKeys.detail(companyId ?? "unknown", vendorId ?? "unknown"),
    queryFn: async () => toVendor(await vendorsApi.get(companyId as string, vendorId as string)),
    enabled: isBrowser && Boolean(companyId) && Boolean(vendorId),
  });
}

/** Invalidation is scoped to the company that changed. */
function useInvalidateCompany(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return () => {
    if (!companyId) return;
    queryClient.invalidateQueries({ queryKey: vendorKeys.list(companyId) });
    // A vendor change records an audit event; refresh the activity views.
    queryClient.invalidateQueries({ queryKey: auditKeys.list(companyId) });
  };
}

export function useCreateVendor(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (vendor: Vendor) =>
      toVendor(await vendorsApi.create(companyId as string, toVendorPayload(vendor))),
    onSuccess: invalidate,
  });
}

export function useUpdateVendor(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (vendor: Vendor) =>
      toVendor(await vendorsApi.update(companyId as string, vendor.id, toVendorPayload(vendor))),
    onSuccess: invalidate,
  });
}

export function useDeactivateVendor(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (id: string) =>
      toVendor(await vendorsApi.deactivate(companyId as string, id)),
    onSuccess: invalidate,
  });
}
