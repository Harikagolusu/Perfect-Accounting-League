/** React Query bindings for GST workings derived from PAL invoices. */

import { useQuery } from "@tanstack/react-query";

import { gstApi, type GstWorkingDto } from "@/lib/api/gst";

const isBrowser = typeof window !== "undefined";

export const gstKeys = {
  working: (companyId: string, from?: string, to?: string) =>
    ["gst", "working", companyId, from ?? "", to ?? ""] as const,
};

export function useGstWorking(
  companyId: string | undefined,
  dateFrom?: string,
  dateTo?: string,
  enabled = true,
) {
  return useQuery<GstWorkingDto>({
    queryKey: gstKeys.working(companyId ?? "", dateFrom, dateTo),
    queryFn: () => gstApi.working(companyId!, dateFrom, dateTo),
    enabled: isBrowser && !!companyId && enabled,
    staleTime: 30_000,
  });
}
