/**
 * React Query bindings for the Companies & Clients feature.
 *
 * Components use these hooks and never call `companiesApi` or the mapper
 * directly, so caching, invalidation and API/UI conversion stay in one layer.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { companiesApi, toCompany, toCompanyPayload } from "@/lib/api/companies";
import type { Company } from "@/lib/types";

export const companyKeys = {
  all: ["companies"] as const,
  list: (includeInactive: boolean) => ["companies", includeInactive] as const,
  detail: (id: string) => ["companies", "detail", id] as const,
};

/**
 * TanStack Start renders on the server, where the backend is not reachable.
 * Fetching in the browser only avoids a hanging SSR pass and a hydration
 * mismatch; the data lands as soon as the component mounts.
 */
const isBrowser = typeof window !== "undefined";

export function useCompanies(includeInactive = true) {
  return useQuery<Company[]>({
    queryKey: companyKeys.list(includeInactive),
    queryFn: async () => (await companiesApi.list(includeInactive)).map(toCompany),
    enabled: isBrowser,
    staleTime: 30_000,
  });
}

export function useCompany(id: string | undefined) {
  return useQuery<Company>({
    queryKey: companyKeys.detail(id ?? "unknown"),
    queryFn: async () => toCompany(await companiesApi.get(id as string)),
    enabled: isBrowser && Boolean(id),
  });
}

/** Mutations always invalidate the whole `companies` namespace so every
 *  list and detail view refetches after a write. */
function useInvalidateCompanies() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: companyKeys.all });
}

export function useCreateCompany() {
  const invalidate = useInvalidateCompanies();
  return useMutation({
    mutationFn: async (company: Company) =>
      toCompany(await companiesApi.create(toCompanyPayload(company))),
    onSuccess: invalidate,
  });
}

export function useUpdateCompany() {
  const invalidate = useInvalidateCompanies();
  return useMutation({
    mutationFn: async (company: Company) =>
      toCompany(await companiesApi.update(company.id, toCompanyPayload(company))),
    onSuccess: invalidate,
  });
}

export function useDeactivateCompany() {
  const invalidate = useInvalidateCompanies();
  return useMutation({
    mutationFn: async (id: string) => toCompany(await companiesApi.deactivate(id)),
    onSuccess: invalidate,
  });
}
