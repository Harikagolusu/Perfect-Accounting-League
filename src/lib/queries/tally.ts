/** React Query bindings for TallyPrime status and sync. */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  tallyApi,
  type TallyBankDataSyncDto,
  type TallyBankSyncDto,
  type TallyCompanySyncDto,
  type TallyMappingSyncDto,
  type TallyStatusDto,
  type TallySyncDto,
} from "@/lib/api/tally";

const isBrowser = typeof window !== "undefined";

export const tallyKeys = {
  status: () => ["tally", "status"] as const,
  latestSync: (companyId: string) => ["tally", "sync", companyId] as const,
};

export function useTallyStatus() {
  return useQuery<TallyStatusDto>({
    queryKey: tallyKeys.status(),
    queryFn: () => tallyApi.status(),
    enabled: isBrowser,
    staleTime: 15_000,
    retry: false,
  });
}

export function useTallyCompanySync() {
  const queryClient = useQueryClient();
  return useMutation<TallyCompanySyncDto, Error, void>({
    mutationFn: () => tallyApi.syncCompanies(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["companies"] });
      queryClient.invalidateQueries({ queryKey: tallyKeys.status() });
    },
  });
}

export function useTallyLatestSync(companyId: string | undefined) {
  return useQuery<{ sync: TallySyncDto | null }>({
    queryKey: tallyKeys.latestSync(companyId ?? ""),
    queryFn: () => tallyApi.latestSync(companyId!),
    enabled: isBrowser && !!companyId,
    staleTime: 30_000,
  });
}

export function useTallySync() {
  const queryClient = useQueryClient();
  return useMutation<TallySyncDto, Error, { companyId: string; tallyCompanyName: string }>({
    mutationFn: ({ companyId, tallyCompanyName }) => tallyApi.sync(companyId, tallyCompanyName),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: tallyKeys.latestSync(data.company_id) });
      queryClient.invalidateQueries({ queryKey: ["ledgers"] });
    },
  });
}

export function useTallyMappingSync() {
  const queryClient = useQueryClient();
  return useMutation<TallyMappingSyncDto, Error, { companyId: string; tallyCompanyName: string }>({
    mutationFn: ({ companyId, tallyCompanyName }) =>
      tallyApi.syncAccountingMappings(companyId, tallyCompanyName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["mappings"] });
      queryClient.invalidateQueries({ queryKey: ["vouchers"] });
      queryClient.invalidateQueries({ queryKey: ["invoices"] });
    },
  });
}

export function useTallyBankSync() {
  const queryClient = useQueryClient();
  return useMutation<TallyBankSyncDto, Error, { companyId: string; tallyCompanyName: string }>({
    mutationFn: ({ companyId, tallyCompanyName }) =>
      tallyApi.syncBankAccounts(companyId, tallyCompanyName),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bankAccounts"] });
    },
  });
}

export function useTallyBankDataSync() {
  const queryClient = useQueryClient();
  return useMutation<
    TallyBankDataSyncDto,
    Error,
    { companyId: string; tallyCompanyName: string; fromDate?: string; toDate?: string }
  >({
    mutationFn: ({ companyId, tallyCompanyName, fromDate, toDate }) =>
      tallyApi.syncBankData(companyId, tallyCompanyName, fromDate, toDate),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bankAccounts"] });
      queryClient.invalidateQueries({ queryKey: ["bankStatements"] });
    },
  });
}
