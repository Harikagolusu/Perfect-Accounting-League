/**
 * React Query bindings for the Document Inbox.
 *
 * The company id is part of every query key and every request, matching the
 * backend's URL-scoped isolation. Invalidations are scoped the same way so a
 * write in one company never refetches another company's inbox.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  documentsApi,
  toInboxDocument,
  type DocumentStatus,
  type DocumentType,
} from "@/lib/api/documents";
import type { InboxDocument } from "@/lib/types";

export const documentKeys = {
  all: ["documents"] as const,
  list: (companyId: string) => ["documents", companyId] as const,
};

/**
 * TanStack Start renders on the server, where the backend is not reachable.
 * Fetching in the browser only avoids a hanging SSR pass and a hydration
 * mismatch; the data lands as soon as the component mounts.
 */
const isBrowser = typeof window !== "undefined";

export function useDocuments(
  companyId: string | undefined,
  filters: { status?: DocumentStatus; documentType?: DocumentType } = {},
) {
  const { status, documentType } = filters;
  return useQuery<InboxDocument[]>({
    queryKey: [...documentKeys.list(companyId ?? "unknown"), { status, documentType }],
    queryFn: async () =>
      (await documentsApi.list(companyId as string, { status, documentType })).map(toInboxDocument),
    enabled: isBrowser && Boolean(companyId),
  });
}

/** Invalidation is scoped to the company whose inbox changed. */
function useInvalidateCompany(companyId: string | undefined) {
  const queryClient = useQueryClient();
  return () => {
    if (companyId) queryClient.invalidateQueries({ queryKey: documentKeys.list(companyId) });
  };
}

export function useUploadDocuments(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (files: File[]) => {
      const uploaded = [];
      // Sequential on purpose: a partial failure should leave the caller with a
      // precise account of which files landed, and inboxes are never large.
      for (const file of files) {
        uploaded.push(toInboxDocument(await documentsApi.upload(companyId as string, file)));
      }
      return uploaded;
    },
    onSuccess: invalidate,
  });
}

export function useDeleteDocument(companyId: string | undefined) {
  const invalidate = useInvalidateCompany(companyId);
  return useMutation({
    mutationFn: async (documentId: string) => {
      await documentsApi.remove(companyId as string, documentId);
      return documentId;
    },
    onSuccess: invalidate,
  });
}
