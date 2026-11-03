/**
 * Bank-transaction entry point into the accounting workflow.
 *
 * Opening a transaction ensures a draft receipt/payment voucher exists, then
 * shows the same review surface as invoice processing: edit the ledgers inline,
 * approve, and slide to post to Tally. No separate "create draft" step.
 */

import { useEffect, useRef } from "react";

import { Section } from "@/components/pal/common";
import { VoucherReview } from "@/components/pal/voucher-review";
import { useCreateVoucherFromBank, useVouchers } from "@/lib/queries/vouchers";
import { toast } from "sonner";

export function BankVoucherPanel({
  companyId,
  statementId,
  transactionId,
}: {
  companyId: string;
  statementId: string;
  transactionId: string;
}) {
  const { data: vouchers = [], isPending } = useVouchers(companyId, {
    sourceType: "BANK_TRANSACTION",
    sourceId: transactionId,
  });
  const createVoucher = useCreateVoucherFromBank(companyId);
  const existing = vouchers[0];
  // Create at most once per transaction; also stops a failed create from looping.
  const tried = useRef<string | null>(null);

  useEffect(() => {
    if (isPending || existing || createVoucher.isPending) return;
    if (tried.current === transactionId) return;
    tried.current = transactionId;
    createVoucher.mutate(
      { transactionId },
      {
        onError: (e) =>
          toast.error(e instanceof Error ? e.message : "Could not create the draft voucher"),
      },
    );
  }, [transactionId, isPending, existing, createVoucher]);

  if (!statementId) return null;

  return (
    <Section title="Accounting Voucher">
      {existing ? (
        <VoucherReview companyId={companyId} voucherId={existing.id} approveAndPost />
      ) : (
        <p className="text-sm text-muted-foreground">
          {createVoucher.isError
            ? "Could not create the draft voucher. Confirm the bank account ledger, then reopen."
            : "Preparing the draft voucher…"}
        </p>
      )}
    </Section>
  );
}
