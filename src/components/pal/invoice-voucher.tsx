/**
 * Invoice-side entry point into the accounting workflow.
 *
 * A document/invoice is not posted automatically. This panel creates an
 * idempotent draft and then hands the human the same review surface used by
 * every other source.
 */

import { useEffect, useRef } from "react";
import { FilePlus2 } from "lucide-react";
import { toast } from "sonner";

import { EmptyState, Section, StatusBadge } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { VoucherReview } from "@/components/pal/voucher-review";
import {
  useCreateVoucherFromInvoice,
  useInvoiceLedgerSuggestion,
  useVouchers,
} from "@/lib/queries/vouchers";

export function InvoiceVoucherPanel({
  companyId,
  invoiceId,
}: {
  companyId: string;
  invoiceId: string;
}) {
  const { data: vouchers = [], isPending } = useVouchers(companyId, {
    sourceType: "INVOICE",
    sourceId: invoiceId,
  });
  const { data: suggestions = [] } = useInvoiceLedgerSuggestion(companyId, invoiceId);
  const createVoucher = useCreateVoucherFromInvoice(companyId);
  const existing = vouchers[0];
  const resuggested = useRef<Set<string>>(new Set());

  // A draft built before its mapping was learned keeps an empty ledger line.
  // Creation is idempotent and re-applies today's suggestions to those lines,
  // so call it once per draft rather than making a reviewer pick by hand.
  const unresolved = existing?.lines.some((line) => !line.ledgerId) ?? false;
  useEffect(() => {
    if (!existing || !unresolved) return;
    if (!["DRAFT", "NEEDS_REVIEW"].includes(existing.status)) return;
    if (createVoucher.isPending || resuggested.current.has(existing.id)) return;
    resuggested.current.add(existing.id);
    createVoucher.mutate({ invoiceId });
    // The draft's unresolved state is the trigger; the mutation object is not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [existing?.id, existing?.status, unresolved, createVoucher.isPending]);

  const create = async () => {
    try {
      const created = await createVoucher.mutateAsync({ invoiceId });
      if (created.status === "NEEDS_REVIEW") {
        toast.warning("Draft created with review exceptions", {
          description: "Resolve the exceptions before submitting it for approval.",
        });
      } else {
        toast.success("Draft voucher created", {
          description: "Review every line before submitting it for approval.",
        });
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not create the draft voucher");
    }
  };

  return (
    <Section title="Accounting Voucher">
      {isPending ? (
        <p className="text-sm text-muted-foreground">Loading voucher...</p>
      ) : existing ? (
        <VoucherReview companyId={companyId} voucherId={existing.id} />
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            Ledger suggestions are explainable. PAL creates a draft from the invoice's canonical
            values and routes any missing evidence to review; it never posts automatically.
          </p>
          {suggestions.length === 0 ? (
            <EmptyState
              title="No ledger evidence yet"
              hint="Configure the company ledger defaults and vendor ledgers, or approve a prior voucher for this vendor."
            />
          ) : (
            <ul className="space-y-2">
              {suggestions.map((suggestion) => (
                <li
                  key={`${suggestion.purpose}-${suggestion.ledgerId ?? suggestion.ledgerName}`}
                  className="flex flex-wrap items-center justify-between gap-2 rounded border p-2 text-sm"
                >
                  <span>
                    <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      {suggestion.purpose}
                    </span>
                    <span className="ml-2 font-medium">{suggestion.ledgerName}</span>
                  </span>
                  <span className="flex items-center gap-2 text-xs text-muted-foreground">
                    <StatusBadge status={suggestion.code ?? suggestion.source} />
                    {suggestion.reason}
                  </span>
                </li>
              ))}
            </ul>
          )}
          <Button onClick={create} disabled={createVoucher.isPending}>
            <FilePlus2 /> Create draft voucher
          </Button>
        </div>
      )}
    </Section>
  );
}
