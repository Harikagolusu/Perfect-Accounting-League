/**
 * Bank-transaction entry point into the accounting workflow.
 *
 * The bank line and the confirmed counterparty line become a draft receipt,
 * payment or contra voucher. PAL never infers an inter-account transfer from
 * narration; the reviewer flags it on the transaction first.
 */

import { useEffect, useState } from "react";
import { FilePlus2, Save } from "lucide-react";
import { toast } from "sonner";

import { EmptyState, Field, Section, StatusBadge } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { VoucherReview } from "@/components/pal/voucher-review";
import { useUpdateBankTransactionMapping } from "@/lib/queries/bankStatements";
import {
  useBankLedgerSuggestion,
  useCreateVoucherFromBank,
  useVouchers,
} from "@/lib/queries/vouchers";
import type { UiBankTransaction } from "@/lib/api/bankStatements";

export function BankVoucherPanel({
  companyId,
  statementId,
  transactionId,
  transaction,
  onTransactionUpdated,
}: {
  companyId: string;
  statementId: string;
  transactionId: string;
  transaction?: UiBankTransaction;
  onTransactionUpdated?: (transaction: UiBankTransaction) => void;
}) {
  const { data: vouchers = [], isPending } = useVouchers(companyId, {
    sourceType: "BANK_TRANSACTION",
    sourceId: transactionId,
  });
  const { data: suggestions = [] } = useBankLedgerSuggestion(companyId, statementId, transactionId);
  const createVoucher = useCreateVoucherFromBank(companyId);
  const updateMapping = useUpdateBankTransactionMapping(companyId);
  const existing = vouchers[0];
  const [counterpartyName, setCounterpartyName] = useState(transaction?.counterpartyName ?? "");
  const [counterpartyLedger, setCounterpartyLedger] = useState(
    transaction?.counterpartyLedger ?? "",
  );
  const [isInterAccount, setIsInterAccount] = useState(
    transaction?.isInterAccountTransfer ?? false,
  );

  useEffect(() => {
    setCounterpartyName(transaction?.counterpartyName ?? "");
    setCounterpartyLedger(transaction?.counterpartyLedger ?? "");
    setIsInterAccount(transaction?.isInterAccountTransfer ?? false);
  }, [
    transaction?.id,
    transaction?.counterpartyName,
    transaction?.counterpartyLedger,
    transaction?.isInterAccountTransfer,
  ]);

  const saveMapping = async () => {
    try {
      const updated = await updateMapping.mutateAsync({
        statementId,
        transactionId,
        body: {
          counterparty_name: counterpartyName.trim() || null,
          counterparty_ledger: counterpartyLedger.trim() || null,
          is_inter_account_transfer: isInterAccount,
        },
      });
      onTransactionUpdated?.(updated);
      toast.success("Counterparty mapping saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the counterparty mapping");
    }
  };

  const create = async () => {
    try {
      const created = await createVoucher.mutateAsync({ transactionId });
      if (created.status === "NEEDS_REVIEW") {
        toast.warning("Draft created with review exceptions", {
          description: "Confirm the counterparty ledger and resolve the exceptions.",
        });
      } else {
        toast.success("Draft voucher created", {
          description: "Review the counterparty ledger before submitting for approval.",
        });
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not create the draft voucher");
    }
  };

  if (!statementId) {
    return null;
  }

  return (
    <Section title="Accounting Voucher">
      {isPending ? (
        <p className="text-sm text-muted-foreground">Loading voucher...</p>
      ) : existing ? (
        <VoucherReview companyId={companyId} voucherId={existing.id} />
      ) : (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            A bank transaction creates a receipt or payment draft. A reviewer must flag an
            inter-account transfer explicitly; PAL does not guess it from the narration.
          </p>
          <div className="grid gap-2 rounded border bg-muted/20 p-3 md:grid-cols-2">
            <Field label="Counterparty name">
              <Input
                value={counterpartyName}
                onChange={(event) => setCounterpartyName(event.target.value)}
              />
            </Field>
            <Field label="Counterparty ledger (from Ledger Master)">
              <Input
                value={counterpartyLedger}
                onChange={(event) => setCounterpartyLedger(event.target.value)}
                placeholder="Exact ledger name"
              />
            </Field>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={isInterAccount}
                onChange={(event) => setIsInterAccount(event.target.checked)}
              />
              Inter-account transfer
            </label>
            <div className="flex items-end">
              <Button
                variant="outline"
                size="sm"
                onClick={saveMapping}
                disabled={updateMapping.isPending}
              >
                <Save /> Save mapping
              </Button>
            </div>
          </div>
          {suggestions.length === 0 ? (
            <EmptyState
              title="No ledger evidence yet"
              hint="Confirm the counterparty ledger on the transaction and configure the bank account ledger."
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
