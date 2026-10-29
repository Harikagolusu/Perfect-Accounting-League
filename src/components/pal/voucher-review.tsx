/**
 * Human review surface for one draft voucher.
 *
 * The component never calculates a posting or changes a status directly. Every
 * action calls the backend lifecycle endpoint, and the deterministic validation
 * result is shown exactly as returned.
 */

import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Plus, RotateCcw, Save, Send, Trash2, Upload, X } from "lucide-react";
import { toast } from "sonner";

import { EmptyState, Field, Section, StatusBadge } from "@/components/pal/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useLedgers } from "@/lib/queries/ledgers";
import { useStore } from "@/lib/store";
import { periodMonthStart } from "@/lib/constants";
import {
  useDeleteVoucher,
  usePostVoucherToTally,
  useUpdateVoucher,
  useVoucher,
  useVoucherAction,
  useVoucherAudit,
  useVerifyVoucherPosting,
} from "@/lib/queries/vouchers";
import {
  toLinePayload,
  voucherStatusLabel,
  voucherTypeLabel,
  type VoucherLine,
  type VoucherSide,
} from "@/lib/api/vouchers";
import { fmtDate } from "@/lib/format";

const editableStatuses = new Set(["DRAFT", "NEEDS_REVIEW", "REJECTED"]);
const submitStatuses = new Set(["DRAFT", "NEEDS_REVIEW"]);
const approveStatuses = new Set(["PENDING_APPROVAL"]);
const rejectStatuses = new Set(["PENDING_APPROVAL", "NEEDS_REVIEW"]);
const reopenStatuses = new Set(["REJECTED", "POSTING_FAILED", "NEEDS_REVIEW"]);
//: Posting to Tally is the only route out of APPROVED. POSTING_FAILED is a
//: retry of a post Tally refused; POSTED is terminal and offers no button.
const postStatuses = new Set(["APPROVED", "POSTING_FAILED"]);
//: Manual confirmation still serves the bulk import-file route and a post left
//: stuck in flight. A failed direct post is retried with the button instead.
const verifyStatuses = new Set(["AWAITING_IMPORT_CONFIRMATION", "POSTING_IN_PROGRESS"]);

/**
 * Collapse a ledger name to a comparison key. The backend resolves names by
 * exact (case-insensitive) match, so "Freight Inward A/c" never matches
 * "Freight Inward"; this catches those near-misses in the reviewer's dropdown.
 */
const ledgerKey = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, "");

const blankLine = (lineNumber: number): VoucherLine => ({
  id: `new-${lineNumber}`,
  companyId: "",
  voucherId: "",
  lineNumber,
  ledgerId: null,
  ledgerName: "",
  side: "Dr",
  amount: "",
  narration: "",
  lineType: "LEDGER",
  itemName: "",
  hsnSac: "",
  billedQuantity: "",
  itemRate: "",
  itemRatePer: "",
  itemAmount: "",
  taxType: "",
});

export function VoucherReview({
  companyId,
  voucherId,
  onDeleted,
}: {
  companyId: string;
  voucherId: string;
  onDeleted?: () => void;
}) {
  const { financialYear, period } = useStore();
  const postingMonth = periodMonthStart(financialYear, period);
  const { data: voucher, isPending, isError, error } = useVoucher(companyId, voucherId);
  const { data: ledgers = [] } = useLedgers(companyId);
  const { data: audit = [] } = useVoucherAudit(companyId, voucherId, Boolean(voucher));
  const updateVoucher = useUpdateVoucher(companyId);
  const deleteVoucher = useDeleteVoucher(companyId);
  const submit = useVoucherAction(companyId, "submit");
  const approve = useVoucherAction(companyId, "approve");
  const reject = useVoucherAction(companyId, "reject");
  const reopen = useVoucherAction(companyId, "reopen");
  const verifyPosting = useVerifyVoucherPosting(companyId);
  const postToTally = usePostVoucherToTally(companyId);

  const [lines, setLines] = useState<VoucherLine[]>([]);
  const [narration, setNarration] = useState("");
  const [dirty, setDirty] = useState(false);
  const [actor, setActor] = useState("local-user");
  const [comment, setComment] = useState("");
  const [externalReference, setExternalReference] = useState("");

  useEffect(() => {
    if (!voucher) return;
    setLines(voucher.lines);
    setNarration(voucher.narration);
    setExternalReference(voucher.postingReference);
    setDirty(false);
  }, [voucher?.id, voucher?.updatedAt]); // eslint-disable-line react-hooks/exhaustive-deps

  const activeLedgers = useMemo(() => ledgers.filter((ledger) => ledger.isActive), [ledgers]);

  const ledgerByKey = useMemo(
    () => new Map(activeLedgers.map((ledger) => [ledgerKey(ledger.name), ledger])),
    [activeLedgers],
  );

  // Pre-select a ledger the backend left unresolved. Derived rather than held
  // in state so a ledger refetch can never discard the reviewer's own edits.
  const resolvedLines = useMemo(
    () =>
      lines.map((line) => {
        if (line.ledgerId || !line.ledgerName.trim()) return line;
        const match = ledgerByKey.get(ledgerKey(line.ledgerName));
        return match ? { ...line, ledgerId: match.id, ledgerName: match.name } : line;
      }),
    [lines, ledgerByKey],
  );

  const autoMatchedCount = resolvedLines.filter(
    (line, index) => line.ledgerId !== lines[index]?.ledgerId,
  ).length;

  if (isPending) {
    return <p className="text-sm text-muted-foreground">Loading voucher...</p>;
  }
  if (isError || !voucher) {
    return (
      <EmptyState
        title="Voucher not available"
        hint={error instanceof Error ? error.message : undefined}
      />
    );
  }

  const editable = editableStatuses.has(voucher.status);
  const busy =
    updateVoucher.isPending ||
    deleteVoucher.isPending ||
    submit.isPending ||
    approve.isPending ||
    reject.isPending ||
    reopen.isPending ||
    verifyPosting.isPending ||
    postToTally.isPending;

  const setLine = (index: number, changes: Partial<VoucherLine>) => {
    setDirty(true);
    setLines((current) => current.map((line, i) => (i === index ? { ...line, ...changes } : line)));
  };

  const chooseLedger = (index: number, ledgerId: string) => {
    const ledger = activeLedgers.find((item) => item.id === ledgerId);
    setLine(index, {
      ledgerId: ledger?.id ?? null,
      ledgerName: ledger?.name ?? "",
    });
  };

  const save = async () => {
    try {
      await updateVoucher.mutateAsync({
        id: voucher.id,
        body: {
          narration: narration.trim() || null,
          lines: resolvedLines.map(toLinePayload),
        },
      });
      setDirty(false);
      toast.success("Draft saved");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not save the voucher");
    }
  };

  const runAction = async (
    label: string,
    action: {
      mutateAsync: (args: { id: string; body?: Record<string, unknown> }) => Promise<unknown>;
    },
  ) => {
    try {
      await action.mutateAsync({
        id: voucher.id,
        body: { actor, comment: comment.trim() || null },
      });
      toast.success(label);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : `Could not ${label.toLowerCase()}`);
    }
  };

  const submitForApproval = async () => {
    try {
      const result = (await submit.mutateAsync({
        id: voucher.id,
        body: { actor, comment: comment.trim() || null },
      })) as { status: string };
      if (result.status === "PENDING_APPROVAL") {
        toast.success("Submitted for approval");
      } else {
        toast.warning("Voucher still needs review", {
          description: "Open the review exceptions above before submitting again.",
        });
      }
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not submit the voucher");
    }
  };

  const rejectVoucher = async () => {
    if (!comment.trim()) {
      toast.error("Give a reason so the correction history is useful.");
      return;
    }
    try {
      await reject.mutateAsync({
        id: voucher.id,
        body: { actor, reason: comment.trim() },
      });
      toast.success("Voucher rejected and returned for correction");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not reject the voucher");
    }
  };

  const postNow = async () => {
    try {
      const result = await postToTally.mutateAsync({ id: voucher.id, actor, postingMonth });
      if (!result.success) {
        toast.error("TallyPrime did not accept the voucher", {
          description: `${result.message} The voucher was not posted; you can retry.`,
        });
        return;
      }
      if (result.already_posted) {
        toast.info("Already posted to Tally", {
          description: `Reference ${result.tally_reference ?? "unknown"}. Nothing was sent again.`,
        });
        return;
      }
      toast.success("Posted to TallyPrime", {
        description: `Tally reference ${result.tally_reference ?? "unknown"}.`,
      });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not post the voucher to Tally");
    }
  };

  const verify = async (verified: boolean) => {
    try {
      await verifyPosting.mutateAsync({
        id: voucher.id,
        body: {
          actor,
          verified,
          external_reference: verified ? externalReference.trim() : null,
          reason: comment.trim() || null,
        },
      });
      toast.success(verified ? "Posting verified" : "Posting failure recorded");
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not record posting verification");
    }
  };

  const remove = async () => {
    if (!window.confirm(`Delete draft voucher ${voucher.voucherNumber}?`)) return;
    try {
      await deleteVoucher.mutateAsync(voucher.id);
      toast.success("Draft voucher deleted");
      onDeleted?.();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Could not delete the voucher");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <span className="num text-sm font-semibold">{voucher.voucherNumber}</span>
            <StatusBadge status={voucherStatusLabel(voucher.status)} />
            <span className="text-xs text-muted-foreground">
              {voucherTypeLabel(voucher.voucherType)} · {fmtDate(voucher.voucherDate)}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            Source: {voucher.sourceType.replaceAll("_", " ").toLowerCase()}
            {voucher.approvedBy ? ` · approved by ${voucher.approvedBy}` : ""}
          </p>
        </div>
        <div className="text-right text-xs text-muted-foreground">
          <div>
            Debit <span className="num text-foreground">{voucher.totalDebit}</span>
          </div>
          <div>
            Credit <span className="num text-foreground">{voucher.totalCredit}</span>
          </div>
          <div className={voucher.isBalanced ? "text-success" : "text-destructive"}>
            {voucher.isBalanced ? "Balanced" : "Not balanced"}
          </div>
        </div>
      </div>

      {voucher.validationIssues.length > 0 && (
        <div className="rounded-md border border-warning/40 bg-warning/5 p-3">
          <p className="text-xs font-semibold text-foreground">Review exceptions</p>
          <ul className="mt-1 space-y-1 text-xs text-muted-foreground">
            {voucher.validationIssues.map((issue, index) => (
              <li key={`${issue.code}-${index}`}>
                <span className="num font-medium text-foreground">{issue.code}</span>
                {issue.field ? ` · ${issue.field}` : ""} — {issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="overflow-x-auto rounded border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50 text-left text-xs text-muted-foreground">
            <tr>
              <th className="p-2">Ledger</th>
              <th className="p-2">Dr/Cr</th>
              <th className="p-2 text-right">Amount</th>
              <th className="p-2">Item</th>
              <th className="p-2">HSN/SAC</th>
              <th className="p-2 text-right">Qty</th>
              <th className="p-2 text-right">Rate</th>
              <th className="p-2 text-right">Item amount</th>
              <th className="p-2">Tax type</th>
              <th className="p-2">Narration</th>
              {editable && <th className="w-8 p-2" />}
            </tr>
          </thead>
          <tbody>
            {resolvedLines.map((line, index) => (
              <tr key={line.id || index} className="border-t align-top">
                <td className="min-w-48 p-2">
                  {editable ? (
                    <select
                      className="h-9 w-full rounded-md border border-input bg-background px-2 text-sm"
                      value={line.ledgerId ?? ""}
                      onChange={(event) => chooseLedger(index, event.target.value)}
                    >
                      <option value="">Select ledger</option>
                      {!line.ledgerId && line.ledgerName && (
                        <option value="" disabled>
                          Missing: {line.ledgerName}
                        </option>
                      )}
                      {activeLedgers.map((ledger) => (
                        <option key={ledger.id} value={ledger.id}>
                          {ledger.name}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <span>{line.ledgerName || "No ledger"}</span>
                  )}
                </td>
                <td className="p-2">
                  {editable ? (
                    <select
                      className="h-9 rounded-md border border-input bg-background px-2 text-sm"
                      value={line.side}
                      onChange={(event) =>
                        setLine(index, { side: event.target.value as VoucherSide })
                      }
                    >
                      <option value="Dr">Dr</option>
                      <option value="Cr">Cr</option>
                    </select>
                  ) : (
                    line.side
                  )}
                </td>
                <td className="p-2 text-right">
                  {editable ? (
                    <Input
                      className="num h-9 text-right"
                      inputMode="decimal"
                      value={line.amount}
                      onChange={(event) => setLine(index, { amount: event.target.value })}
                    />
                  ) : (
                    <span className="num">{line.amount}</span>
                  )}
                </td>
                <td className="p-2">
                  {editable ? (
                    <Input
                      className="h-9"
                      value={line.itemName}
                      onChange={(event) => setLine(index, { itemName: event.target.value })}
                    />
                  ) : (
                    <span>{line.itemName || "-"}</span>
                  )}
                </td>
                <td className="p-2">
                  {editable ? (
                    <Input
                      className="num h-9"
                      value={line.hsnSac}
                      onChange={(event) => setLine(index, { hsnSac: event.target.value })}
                    />
                  ) : (
                    <span className="num">{line.hsnSac || "-"}</span>
                  )}
                </td>
                <td className="p-2 text-right">
                  {editable ? (
                    <Input
                      className="num h-9 text-right"
                      inputMode="decimal"
                      value={line.billedQuantity}
                      onChange={(event) => setLine(index, { billedQuantity: event.target.value })}
                    />
                  ) : (
                    <span className="num">{line.billedQuantity || "-"}</span>
                  )}
                </td>
                <td className="p-2 text-right">
                  {editable ? (
                    <Input
                      className="num h-9 text-right"
                      inputMode="decimal"
                      value={line.itemRate}
                      onChange={(event) => setLine(index, { itemRate: event.target.value })}
                    />
                  ) : (
                    <span className="num">{line.itemRate || "-"}</span>
                  )}
                </td>
                <td className="p-2 text-right">
                  {editable ? (
                    <Input
                      className="num h-9 text-right"
                      inputMode="decimal"
                      value={line.itemAmount}
                      onChange={(event) => setLine(index, { itemAmount: event.target.value })}
                    />
                  ) : (
                    <span className="num">{line.itemAmount || "-"}</span>
                  )}
                </td>
                <td className="p-2">
                  {editable ? (
                    <Input
                      className="h-9"
                      value={line.taxType}
                      onChange={(event) => setLine(index, { taxType: event.target.value })}
                    />
                  ) : (
                    <span>{line.taxType || "-"}</span>
                  )}
                </td>
                <td className="p-2">
                  {editable ? (
                    <Input
                      className="h-9"
                      value={line.narration}
                      onChange={(event) => setLine(index, { narration: event.target.value })}
                    />
                  ) : (
                    <span className="text-muted-foreground">{line.narration || "-"}</span>
                  )}
                </td>
                {editable && (
                  <td className="p-2">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        setDirty(true);
                        setLines((current) => current.filter((_, i) => i !== index));
                      }}
                      aria-label="Remove line"
                    >
                      <X className="h-4 w-4" />
                    </Button>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editable && (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setDirty(true);
              setLines((current) => {
                const nextNumber = Math.max(0, ...current.map((line) => line.lineNumber)) + 1;
                return [...current, blankLine(nextNumber)];
              });
            }}
          >
            <Plus /> Add line
          </Button>
          <Input
            className="h-9 max-w-md"
            placeholder="Voucher narration"
            value={narration}
            onChange={(event) => {
              setDirty(true);
              setNarration(event.target.value);
            }}
          />
          <Button size="sm" onClick={save} disabled={busy}>
            <Save /> Save draft
          </Button>
          {dirty ? (
            <span className="text-xs text-warning">
              Unsaved changes — save before submitting for approval.
            </span>
          ) : autoMatchedCount > 0 ? (
            <span className="text-xs text-warning">
              {autoMatchedCount} ledger{autoMatchedCount > 1 ? "s" : ""} matched by name — save the
              draft to keep the selection, or pick a different ledger.
            </span>
          ) : null}
        </div>
      )}

      <div className="grid gap-3 rounded border bg-muted/20 p-3 md:grid-cols-2">
        <Field label="Actor (audit trail)">
          <Input value={actor} onChange={(event) => setActor(event.target.value)} />
        </Field>
        <Field label="Reviewer comment / rejection reason">
          <Input
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            placeholder="Required when rejecting"
          />
        </Field>
      </div>

      <div className="flex flex-wrap gap-2">
        {submitStatuses.has(voucher.status) && (
          <Button
            variant="outline"
            size="sm"
            onClick={submitForApproval}
            disabled={busy || dirty || autoMatchedCount > 0}
          >
            <Send /> Submit for approval
          </Button>
        )}
        {approveStatuses.has(voucher.status) && (
          <Button size="sm" onClick={() => runAction("Voucher approved", approve)} disabled={busy}>
            Approve
          </Button>
        )}
        {rejectStatuses.has(voucher.status) && (
          <Button variant="destructive" size="sm" onClick={rejectVoucher} disabled={busy}>
            Reject
          </Button>
        )}
        {reopenStatuses.has(voucher.status) && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => runAction("Voucher reopened", reopen)}
            disabled={busy}
          >
            <RotateCcw /> Reopen for correction
          </Button>
        )}
        {postStatuses.has(voucher.status) && (
          <Button size="sm" onClick={postNow} disabled={busy}>
            <Upload />
            {postToTally.isPending
              ? "Posting to Tally…"
              : voucher.status === "POSTING_FAILED"
                ? "Retry post to Tally"
                : "Post to Tally"}
          </Button>
        )}
        {voucher.status === "POSTED" && (
          <span className="flex items-center gap-1.5 text-xs text-success">
            <CheckCircle2 className="h-4 w-4" />
            Posted to Tally
            {voucher.postingReference ? ` · ${voucher.postingReference}` : ""}
          </span>
        )}
        {editableStatuses.has(voucher.status) && (
          <Button variant="ghost" size="sm" onClick={remove} disabled={busy}>
            <Trash2 /> Delete draft
          </Button>
        )}
      </div>

      {verifyStatuses.has(voucher.status) && (
        <div className="rounded border border-primary/20 bg-secondary/40 p-3">
          <p className="text-xs font-semibold text-foreground">Verified posting</p>
          <p className="mt-1 text-xs text-muted-foreground">
            Exporting does not post. Record the external reference only after the voucher has
            actually been imported and checked in Tally/Zoho.
          </p>
          <div className="mt-2 flex flex-wrap items-end gap-2">
            <Field label="External posting reference">
              <Input
                className="num"
                value={externalReference}
                onChange={(event) => setExternalReference(event.target.value)}
                placeholder="Tally voucher id"
              />
            </Field>
            <Button
              size="sm"
              onClick={() => verify(true)}
              disabled={busy || !externalReference.trim()}
            >
              Mark posted
            </Button>
            <Button variant="outline" size="sm" onClick={() => verify(false)} disabled={busy}>
              Record failure
            </Button>
          </div>
        </div>
      )}

      <Section title="Audit trail">
        {audit.length === 0 ? (
          <EmptyState title="No audit events yet" />
        ) : (
          <div className="max-h-64 space-y-2 overflow-y-auto">
            {audit.map((event) => (
              <div key={event.id} className="border-b pb-2 text-xs last:border-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-foreground">{event.eventType}</span>
                  <span className="text-muted-foreground">{event.actor}</span>
                  <span className="text-muted-foreground">{fmtDate(event.createdAt)}</span>
                </div>
                {event.previousStatus || event.newStatus ? (
                  <p className="mt-0.5 text-muted-foreground">
                    {event.previousStatus ?? "-"} → {event.newStatus ?? "-"}
                  </p>
                ) : null}
                {event.field ? (
                  <p className="mt-0.5 text-muted-foreground">
                    {event.field}: {event.oldValue ?? "-"} → {event.newValue ?? "-"}
                  </p>
                ) : null}
                {event.reason ? (
                  <p className="mt-0.5 text-muted-foreground">{event.reason}</p>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}
