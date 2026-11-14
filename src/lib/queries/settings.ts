/** React Query bindings for the per-company Settings document. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { settingsApi, type CompanySettingsDto, type SettingsDoc } from "@/lib/api/settings";

const isBrowser = typeof window !== "undefined";

export const settingsKeys = {
  byCompany: (companyId: string) => ["settings", companyId] as const,
};

export function useCompanySettings(companyId: string | undefined) {
  return useQuery<CompanySettingsDto>({
    queryKey: settingsKeys.byCompany(companyId ?? ""),
    queryFn: () => settingsApi.get(companyId!),
    enabled: isBrowser && !!companyId,
    staleTime: 30_000,
  });
}

export function useSaveCompanySettings(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation<CompanySettingsDto, Error, SettingsDoc>({
    mutationFn: (data) => settingsApi.save(companyId!, data),
    onSuccess: (dto) => {
      queryClient.setQueryData(settingsKeys.byCompany(companyId ?? ""), dto);
    },
  });
}
